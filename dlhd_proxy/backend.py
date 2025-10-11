import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import logging
from urllib.parse import urlparse

import httpx
from dateutil import parser
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from xml.etree.ElementTree import Element, SubElement, tostring

from dlhd_proxy.step_daddy import Channel, StepDaddy
from dlhd_proxy import settings as app_settings
from rxconfig import config
from .utils import urlsafe_base64_decode

GUIDE_FILE = Path("guide.xml")
DATA_DIR = Path(os.getenv("CHANNEL_DATA_DIR", "data"))
CHANNEL_FILE = Path(
    os.getenv("CHANNEL_FILE", str(DATA_DIR / "selected_channels.json"))
)
LEGACY_CHANNEL_FILE = Path("channels.json")
LOG_FILE = Path("dlhd_proxy.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app_settings.apply_initial_settings()

fastapi_app = FastAPI()


@fastapi_app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Log 404 errors and return a standard response."""
    logger.warning("404 Not Found: %s", request.url.path)
    return JSONResponse({"detail": "Not Found"}, status_code=status.HTTP_404_NOT_FOUND)


step_daddy = StepDaddy()
client = httpx.AsyncClient(
    http2=True,
    timeout=httpx.Timeout(15.0, read=60.0),
    follow_redirects=True,
)


@fastapi_app.on_event("startup")
async def _startup() -> None:
    """Ensure we have an initial channel list on boot."""
    if not step_daddy.channels:
        try:
            await step_daddy.load_channels()
        except Exception:
            logger.exception("Initial channel load failed")


@fastapi_app.on_event("shutdown")
async def _shutdown() -> None:
    """Close shared HTTP clients cleanly."""
    await client.aclose()
    await step_daddy.aclose()


def _load_channel_file(path: Path) -> set[str] | None:
    """Return the channel IDs stored at *path* or ``None`` on error."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("Channel selection file %s is not valid JSON", path)
        return None
    except OSError as exc:
        logger.warning("Unable to read channel selection file %s: %s", path, exc)
        return None
    if isinstance(raw, list):
        return {str(ch) for ch in raw}
    logger.warning("Channel selection file %s contained unexpected data: %s", path, type(raw))
    return None


def _write_channel_file(path: Path, payload: list[str]) -> None:
    """Persist the channel IDs to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def get_selected_channel_ids() -> set[str]:
    """Return the set of enabled channel IDs."""
    for path in (CHANNEL_FILE, LEGACY_CHANNEL_FILE):
        data = _load_channel_file(path)
        if data is not None:
            if path is LEGACY_CHANNEL_FILE and not CHANNEL_FILE.exists():
                try:
                    _write_channel_file(CHANNEL_FILE, sorted(data))
                except OSError as exc:
                    logger.warning("Unable to migrate channel selection to %s: %s", CHANNEL_FILE, exc)
            return data
    return {ch.id for ch in step_daddy.channels}


def set_selected_channel_ids(ids: list[str]) -> None:
    """Persist the selected channel IDs and refresh the guide."""
    cleaned = sorted({str(cid) for cid in ids if cid})
    try:
        _write_channel_file(CHANNEL_FILE, cleaned)
    except OSError as exc:
        logger.exception("Failed to persist channel selection")
        raise RuntimeError("Unable to save channel selection") from exc
    if LEGACY_CHANNEL_FILE != CHANNEL_FILE:
        try:
            _write_channel_file(LEGACY_CHANNEL_FILE, cleaned)
        except OSError as exc:
            logger.warning("Unable to update legacy channel selection file: %s", exc)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(generate_guide())
    except RuntimeError:
        asyncio.run(generate_guide())


@fastapi_app.get("/stream/{channel_id}.m3u8")
async def stream(channel_id: str):
    if not channel_id:
        return JSONResponse(
            content={"error": "Channel id is required"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        playlist_body = await step_daddy.stream(channel_id)
        return Response(
            content=playlist_body,
            media_type="application/vnd.apple.mpegurl",
            headers={"Content-Disposition": f"attachment; filename={channel_id}.m3u8"},
        )
    except ValueError as exc:
        logger.warning("Stream not available for %s: %s", channel_id, exc)
        return JSONResponse(
            content={"error": "Stream not found"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as e:
        logger.exception("Stream error for %s", channel_id)
        return JSONResponse(content={"error": str(e)}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@fastapi_app.get("/key/{url}/{host}")
async def key(url: str, host: str):
    try:
        return Response(
            content=await step_daddy.key(url, host),
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=key"}
        )
    except Exception as e:
        logger.exception("Key request failed")
        return JSONResponse(content={"error": str(e)}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@fastapi_app.get("/content/{path}")
async def content(path: str):
    try:
        url = step_daddy.content_url(path)
    except Exception as exc:
        logger.warning("Invalid content path provided: %s", exc)
        return JSONResponse(
            content={"error": "Invalid content request"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        response = await client.send(
            client.build_request("GET", url),
            stream=True,
            timeout=60,
        )
    except httpx.RequestError as exc:
        logger.exception("Content proxy request failed for %s", url)
        return JSONResponse(
            content={"error": "Unable to reach upstream content"},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    if response.status_code >= 400:
        logger.warning(
            "Upstream content request returned %s for %s", response.status_code, url
        )
        await response.aclose()
        return JSONResponse(
            content={"error": "Upstream content returned an error"},
            status_code=response.status_code,
        )

    media_type = response.headers.get("content-type", "application/octet-stream")
    return StreamingResponse(
        response.aiter_bytes(chunk_size=64 * 1024),
        media_type=media_type,
        background=BackgroundTask(response.aclose),
    )


async def update_channels():
    while True:
        try:
            await step_daddy.load_channels()
            logger.info("Channels refreshed")
        except asyncio.CancelledError:
            logger.info("Channel refresh task cancelled")
            break
        except Exception:
            logger.exception("Failed to update channels")
            await asyncio.sleep(60)
            continue
        await asyncio.sleep(300)


def get_channels() -> list[Channel]:
    """Return a copy of the loaded channels."""
    return list(step_daddy.channels)


def get_enabled_channels() -> list[Channel]:
    """Return only the channels that are currently enabled."""
    selected = get_selected_channel_ids()
    return [ch for ch in step_daddy.channels if ch.id in selected]


def get_public_url() -> str:
    """Return the resolved public URL used for playlist and stream links."""

    return app_settings.get_public_url()


def update_public_url(url: str) -> str:
    """Persist a new public URL and return the active value."""

    return app_settings.set_public_url(url)


def public_url_has_env_override() -> bool:
    """Return ``True`` if environment variables override the stored public URL."""

    return app_settings.has_env_override()


def get_channel(channel_id: str) -> Channel | None:
    """Return the channel with the given ID if it exists."""
    if not channel_id:
        return None
    return next((channel for channel in step_daddy.channels if channel.id == channel_id), None)


@fastapi_app.get("/playlist.m3u8")
def playlist():
    selected = get_selected_channel_ids()
    channels = [ch for ch in step_daddy.channels if ch.id in selected]
    return Response(
        content=step_daddy.playlist(channels),
        media_type="application/vnd.apple.mpegurl",
        headers={"Content-Disposition": "attachment; filename=playlist.m3u8"},
    )


async def get_schedule():
    try:
        schedule = await step_daddy.schedule()
    except Exception:
        logger.exception("Failed to fetch upstream schedule")
        return {}
    selected = get_selected_channel_ids()

    # Build lookup maps for resolving channel name/id mismatches.
    id_to_name = {ch.id: ch.name for ch in step_daddy.channels}

    def norm(name: str) -> str:
        """Return a simplified version of a channel name."""
        return re.sub(r"\W+", "", name or "").lower()

    name_to_id: dict[str, str] = {}
    for ch in step_daddy.channels:
        key = norm(ch.name)
        # Prefer selected channels when multiple share the same name.
        if key not in name_to_id or ch.id in selected:
            name_to_id[key] = ch.id

    def filter_channels(data):
        def resolve(chan: dict):
            cid = str(chan.get("channel_id", ""))
            name = chan.get("channel_name", "")
            mapped = name_to_id.get(norm(name))
            if id_to_name.get(cid) != name:
                if mapped:
                    cid = mapped
                else:
                    return None
            if cid in selected:
                chan = chan.copy()
                chan["channel_id"] = cid
                return chan
            return None

        if isinstance(data, list):
            return [c for c in (resolve(x) for x in data) if c]
        if isinstance(data, dict):
            return {k: v for k, v in ((k, resolve(v)) for k, v in data.items()) if v}
        return []

    filtered = {}
    for day, categories in schedule.items():
        for category, events in categories.items():
            new_events = []
            for event in events:
                ch1 = filter_channels(event.get("channels"))
                ch2 = filter_channels(event.get("channels2"))
                if not ch1 and not ch2:
                    continue
                e = event.copy()
                if ch1:
                    e["channels"] = ch1
                else:
                    e.pop("channels", None)
                if ch2:
                    e["channels2"] = ch2
                else:
                    e.pop("channels2", None)
                new_events.append(e)
            if new_events:
                filtered.setdefault(day, {})[category] = new_events
    return filtered


@fastapi_app.get("/logo/{logo}")
async def logo(logo: str):
    try:
        url = urlsafe_base64_decode(logo)
    except Exception as exc:
        logger.warning("Invalid logo token provided: %s", exc)
        return JSONResponse(
            content={"error": "Invalid logo reference"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    filename = Path(urlparse(url).path).name or "logo"
    logo_dir = Path("logo-cache")
    try:
        logo_dir.mkdir(exist_ok=True)
    except OSError as exc:
        logger.exception("Failed to create logo cache directory")
        return JSONResponse(
            content={"error": "Internal server error"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    cache_path = logo_dir / filename
    if cache_path.exists():
        return FileResponse(cache_path)

    try:
        response = await client.get(
            url,
            headers={
                "user-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0"
            },
        )
    except httpx.ConnectTimeout:
        return JSONResponse(
            content={"error": "Request timed out"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.RequestError as exc:
        logger.exception("Logo download failed for %s", url)
        return JSONResponse(
            content={"error": "Failed to download logo"},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    if response.status_code != status.HTTP_200_OK:
        return JSONResponse(
            content={"error": "Logo not found"},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        cache_path.write_bytes(response.content)
    except OSError:
        logger.exception("Failed to cache logo to disk")
        media_type = response.headers.get("content-type", "image/png")
        return Response(content=response.content, media_type=media_type)

    return FileResponse(cache_path)


async def generate_guide():
    """Fetch schedule and write an updated guide.xml file."""
    schedule = await get_schedule()
    selected = get_selected_channel_ids()

    root = Element("tv", attrib={"generator-info-name": "dlhd-proxy"})
    added_channels = set()
    display_names = {ch.id: ch.name for ch in step_daddy.channels}

    # Known channels with logos
    for ch in step_daddy.channels:
        if ch.id not in selected:
            continue
        channel_elem = SubElement(root, "channel", id=ch.id)
        SubElement(channel_elem, "display-name").text = ch.name
        if ch.logo:
            SubElement(channel_elem, "icon", src=ch.logo)
        added_channels.add(ch.id)

    def ensure_channel(channel: dict):
        cid = channel.get("channel_id")
        if cid and cid in selected and cid not in added_channels:
            elem = SubElement(root, "channel", id=cid)
            display_name = display_names.get(cid) or channel.get("channel_name", cid)
            SubElement(elem, "display-name").text = display_name
            added_channels.add(cid)

    def iter_channels(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())
        return []

    utc = ZoneInfo("UTC")

    for day, categories in schedule.items():
        date = parser.parse(day.split(" - ")[0], dayfirst=True)
        for category, events in categories.items():
            for event in events:
                time_str = event.get("time")
                if not time_str:
                    logger.debug("Skipping schedule entry without a start time: %s", event)
                    continue
                try:
                    hour, minute = map(int, time_str.split(":"))
                except ValueError:
                    logger.debug("Invalid schedule time '%s' for event %s", time_str, event)
                    continue
                start = date.replace(hour=hour, minute=minute, tzinfo=utc)
                stop = start + timedelta(hours=1)
                for channel in iter_channels(event.get("channels")) + iter_channels(event.get("channels2")):
                    ensure_channel(channel)
                    programme = SubElement(
                        root,
                        "programme",
                        start=start.strftime("%Y%m%d%H%M%S +0000"),
                        stop=stop.strftime("%Y%m%d%H%M%S +0000"),
                        channel=channel.get("channel_id"),
                    )
                    SubElement(programme, "title", lang="en").text = event.get("event") or "Unknown"
                    SubElement(programme, "category").text = category

    xml_data = tostring(root, encoding="utf-8", xml_declaration=True)
    GUIDE_FILE.write_bytes(xml_data)
    logger.info("guide.xml generated")


@fastapi_app.get("/guide.xml")
async def guide():
    """Return the cached XMLTV guide, generating it if needed."""
    if not GUIDE_FILE.exists():
        await generate_guide()
    return FileResponse(GUIDE_FILE)


async def auto_update_guide():
    """Update guide.xml once per day at the configured time."""
    hour, minute = map(int, config.guide_update.split(":"))
    tz = ZoneInfo(config.timezone)
    if not GUIDE_FILE.exists():
        try:
            await generate_guide()
        except Exception:
            logger.exception("Initial guide generation failed")
    while True:
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await generate_guide()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Automatic guide update failed")
            continue


async def refresh_all():
    """Manually refresh channels and guide."""
    logger.info("Manual refresh requested")
    await step_daddy.load_channels()
    await generate_guide()
    logger.info("Manual refresh complete")


@fastapi_app.post("/refresh")
async def refresh():
    try:
        await refresh_all()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.exception("Manual refresh failed")
        return JSONResponse({"error": str(e)}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@fastapi_app.get("/logs")
def logs():
    """Return the application log file, creating it if missing."""
    if not LOG_FILE.exists():
        # Ensure an empty log file exists so the endpoint never 404s
        LOG_FILE.touch()
    return FileResponse(LOG_FILE)

