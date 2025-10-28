import json
import html
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Iterable, List
from urllib.parse import parse_qs, quote, urlparse, urlsplit

import reflex as rx
from curl_cffi import AsyncSession

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency
    BeautifulSoup = None

from .utils import decode_bundle, decrypt, encrypt, urlsafe_base64
from rxconfig import config


logger = logging.getLogger(__name__)

PROXYABLE_HLS_EXTENSIONS = {
    ".m3u8",
    ".ts",
    ".aac",
    ".vtt",
    ".m4s",
    ".m4a",
    ".mp4",
    ".mp3",
}


def _is_hls_path(path: str) -> bool:
    """Return ``True`` when *path* points to a proxyable HLS asset."""

    suffix = Path(path).suffix.lower()
    return suffix in PROXYABLE_HLS_EXTENSIONS


class Channel(rx.Base):
    id: str
    name: str
    tags: List[str]
    logo: str | None


class StepDaddy:
    def __init__(self):
        socks5 = config.socks5
        if socks5 != "":
            self._session = AsyncSession(proxy="socks5://" + socks5)
        else:
            self._session = AsyncSession()
        self._base_url = "https://daddylivestream.com"
        self.channels: list[Channel] = []
        try:
            meta_data = resources.files(__package__).joinpath("meta.json").read_text()
            self._meta = json.loads(meta_data)
        except Exception:
            self._meta = {}
        self._logged_domains = {"daddylivestream.com", "dlhd.dad"}

    def _headers(self, referer: str = None, origin: str = None):
        if referer is None:
            referer = self._base_url
        headers = {
            "Referer": referer,
            "user-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
        }
        if origin:
            headers["Origin"] = origin
        return headers

    async def load_channels(self):
        channels: list[Channel] = []
        url = f"{self._base_url}/24-7-channels.php"
        try:
            response = await self._get(url, headers=self._headers())
            if response.status_code >= 400:
                raise ValueError(
                    f"Failed to load channels: HTTP {response.status_code}"
                )
            matches = re.findall(
                r'href="/watch\.php\?id=(\d+)"[^>]*>\s*<div class="card__title">(.*?)</div>',
                response.text,
                re.DOTALL,
            )
            seen_ids = set()
            for channel_id, channel_name in matches:
                if channel_id in seen_ids:
                    continue
                seen_ids.add(channel_id)
                name = html.unescape(channel_name.strip()).replace("#", "")
                meta_key = "18+" if name.startswith("18+") else name
                meta = self._meta.get(meta_key, {})
                logo = meta.get("logo", "")
                if logo:
                    logo = f"{config.api_url}/logo/{urlsafe_base64(logo)}"
                channels.append(
                    Channel(
                        id=channel_id,
                        name=name,
                        tags=meta.get("tags", []),
                        logo=logo,
                    )
                )
            logger.info("Loaded %d channels from daddylivestream.com", len(channels))
        finally:
            self._enumerate_duplicate_names(channels)
            self.channels = sorted(
                channels,
                key=lambda channel: (channel.name.startswith("18"), channel.name),
            )

    async def stream(self, channel_id: str):
        key = "CHANNEL_KEY"
        url = f"{self._base_url}/stream/stream-{channel_id}.php"
        response = await self._get(url, headers=self._headers())
        matches = re.compile("iframe src=\"(.*)\" width").findall(response.text)
        if matches:
            source_url = matches[0]
            source_response = await self._get(source_url, headers=self._headers(url))
        else:
            raise ValueError("Failed to find source URL for channel")

        channel_key = re.compile(rf"const\s+{re.escape(key)}\s*=\s*\"(.*?)\";").findall(source_response.text)[-1]

        data = decode_bundle(source_response.text)
        auth_ts = data.get("b_ts", "")
        auth_sig = data.get("b_sig", "")
        auth_rnd = data.get("b_rnd", "")
        auth_url = data.get("b_host", "")
        auth_request_url = f"{auth_url}auth.php?channel_id={channel_key}&ts={auth_ts}&rnd={auth_rnd}&sig={auth_sig}"
        auth_response = await self._get(
            auth_request_url, headers=self._headers(source_url)
        )
        if auth_response.status_code != 200:
            raise ValueError("Failed to get auth response")
        key_url = urlparse(source_url)
        key_url = f"{key_url.scheme}://{key_url.netloc}/server_lookup.php?channel_id={channel_key}"
        key_response = await self._get(key_url, headers=self._headers(source_url))
        server_key = key_response.json().get("server_key")
        if not server_key:
            raise ValueError("No server key found in response")
        if server_key == "top1/cdn":
            server_url = f"https://top1.newkso.ru/top1/cdn/{channel_key}/mono.m3u8"
        else:
            server_url = f"https://{server_key}new.newkso.ru/{server_key}/{channel_key}/mono.m3u8"
        m3u8 = await self._get(
            server_url, headers=self._headers(quote(str(source_url)))
        )
        rewritten_lines: list[str] = []
        segment_start_index: int | None = None
        for line in m3u8.text.splitlines():
            if line.startswith("#EXT-X-KEY:"):
                original_url = re.search(r'URI="(.*?)"', line).group(1)
                line = line.replace(
                    original_url,
                    f"{config.api_url}/key/{encrypt(original_url)}/{encrypt(urlparse(source_url).netloc)}",
                )
                rewritten_lines.append(line)
                continue

            if line.startswith("#EXTINF") or line.startswith("#EXT-X-PROGRAM-DATE-TIME") or line.startswith(
                "#EXT-X-BYTERANGE"
            ):
                if segment_start_index is None:
                    segment_start_index = len(rewritten_lines)
                rewritten_lines.append(line)
                continue

            if line.startswith("http"):
                parsed_url = urlparse(line)
                path = (parsed_url.path or "").lower()
                if _is_hls_path(path):
                    rewritten_lines.append(f"{config.api_url}/content/{encrypt(line)}")
                    segment_start_index = None
                    continue

                if segment_start_index is not None:
                    del rewritten_lines[segment_start_index:]
                    segment_start_index = None
                continue

            segment_start_index = None
            rewritten_lines.append(line)

        return "\n".join(rewritten_lines) + "\n"

    async def key(self, url: str, host: str):
        url = decrypt(url)
        host = decrypt(host)
        response = await self._get(
            url, headers=self._headers(f"{host}/", host), timeout=60
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get key")
        return response.content

    @staticmethod
    def content_url(path: str):
        return decrypt(path)

    def playlist(self, channels: Iterable[Channel] | None = None):
        data = "#EXTM3U\n"
        channels = list(channels) if channels is not None else self.channels
        for channel in channels:
            entry = f" tvg-logo=\"{channel.logo}\",{channel.name}" if channel.logo else f",{channel.name}"
            data += f"#EXTINF:-1{entry}\n{config.api_url}/stream/{channel.id}.m3u8\n"
        return data

    async def schedule(self):
        json_url = f"{self._base_url}/schedule/schedule-generated.php"
        response = await self._get(json_url, headers=self._headers())
        if response.status_code < 400:
            return response.json()

        if response.status_code not in {401, 403, 404}:
            raise ValueError(
                f"Failed to fetch schedule: HTTP {response.status_code}"
            )

        for path in ("/schedule", "/"):
            try:
                html_response = await self._get(
                    f"{self._base_url}{path}", headers=self._headers()
                )
            except Exception as exc:  # pragma: no cover - network failure
                logger.debug("Schedule fallback request to %s failed: %s", path, exc)
                continue
            if html_response.status_code >= 400:
                logger.debug(
                    "Schedule fallback %s returned HTTP %s",
                    path,
                    html_response.status_code,
                )
                continue
            try:
                schedule = self._parse_schedule_html(html_response.text)
            except ValueError as exc:
                logger.debug(
                    "Unable to parse schedule HTML from %s: %s", path, exc
                )
                continue
            if schedule:
                return schedule

        raise ValueError("Failed to fetch schedule: no usable response")

    @staticmethod
    def _parse_schedule_html(payload: str) -> dict[str, dict[str, list[dict]]]:
        if BeautifulSoup is None:
            raise ValueError("BeautifulSoup is required to parse schedule HTML")

        soup = BeautifulSoup(payload, "html.parser")
        container = soup.select_one("div.schedule")
        if not container:
            raise ValueError("Schedule container not found")

        schedule: dict[str, dict[str, list[dict]]] = {}

        for day in container.select("div.schedule__day"):
            title = day.select_one("div.schedule__dayTitle")
            day_name = title.get_text(strip=True) if title else ""
            if not day_name:
                continue

            categories: dict[str, list[dict]] = {}

            for category in day.select("div.schedule__category"):
                header = category.select_one(".schedule__catHeader .card__meta")
                category_name = header.get_text(strip=True) if header else ""
                if not category_name:
                    continue

                events: list[dict] = []

                for event in category.select("div.schedule__event"):
                    event_header = event.select_one(".schedule__eventHeader")
                    if not event_header:
                        continue

                    time_node = event_header.select_one(".schedule__time")
                    time_value = ""
                    if time_node:
                        time_value = time_node.get("data-time", "").strip() or time_node.get_text(strip=True)

                    title_node = event_header.select_one(".schedule__eventTitle")
                    event_title = ""
                    if title_node:
                        event_title = title_node.get_text(strip=True)
                    event_title = event_title or event_header.get("data-title", "").strip()
                    if not event_title:
                        continue

                    channels: list[dict[str, str]] = []
                    channel_container = event.select_one(".schedule__channels")
                    if channel_container:
                        for link in channel_container.find_all("a"):
                            href = link.get("href", "")
                            channel_id = ""
                            if href:
                                parsed = urlsplit(href)
                                if parsed.query:
                                    params = parse_qs(parsed.query)
                                    ids = params.get("id") or params.get("channel")
                                    if ids:
                                        channel_id = ids[0]
                                if not channel_id:
                                    match = re.search(r"(\d+)", href)
                                    if match:
                                        channel_id = match.group(1)
                            name = (link.get("title") or link.get_text()).strip()
                            if not channel_id or not name:
                                continue
                            channels.append(
                                {"channel_id": str(channel_id), "channel_name": name}
                            )

                    if not channels:
                        continue

                    event_data: dict[str, object] = {
                        "time": time_value,
                        "event": event_title,
                        "channels": channels,
                    }

                    alt_container = event.select_one(
                        ".schedule__channels--alternate, .schedule__channelsAlt"
                    )
                    if alt_container:
                        alt_channels: list[dict[str, str]] = []
                        for link in alt_container.find_all("a"):
                            href = link.get("href", "")
                            match = re.search(r"(\d+)", href)
                            if not match:
                                continue
                            name = (link.get("title") or link.get_text()).strip()
                            if not name:
                                continue
                            alt_channels.append(
                                {
                                    "channel_id": match.group(1),
                                    "channel_name": name,
                                }
                            )
                        if alt_channels:
                            event_data["channels2"] = alt_channels

                    events.append(event_data)

                if events:
                    categories[category_name] = events

            if categories:
                schedule[day_name] = categories

        if not schedule:
            raise ValueError("No schedule data located")

        return schedule

    async def aclose(self) -> None:
        await self._session.close()

    def _should_log_url(self, url: str) -> bool:
        netloc = urlsplit(url).netloc.lower()
        return any(netloc.endswith(domain) for domain in self._logged_domains)

    async def _get(self, url: str, **kwargs):
        try:
            response = await self._session.get(url, **kwargs)
        except Exception:
            if self._should_log_url(url):
                logger.exception("Request to %s failed", url)
            raise
        if self._should_log_url(url):
            if response.status_code >= 400:
                logger.warning(
                    "Request to %s returned HTTP %s", url, response.status_code
                )
            else:
                logger.info(
                    "Request to %s succeeded with HTTP %s", url, response.status_code
                )
        return response

    @staticmethod
    def _enumerate_duplicate_names(channels: Iterable[Channel]) -> None:
        channel_list = list(channels)
        counts: dict[str, int] = {}
        for channel in channel_list:
            counts[channel.name] = counts.get(channel.name, 0) + 1

        seen: dict[str, int] = {}
        for channel in channel_list:
            if counts[channel.name] > 1:
                seen[channel.name] = seen.get(channel.name, 0) + 1
                channel.name = f"{channel.name} ({seen[channel.name]})"
