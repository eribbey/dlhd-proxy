import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin, urlparse

import reflex as rx
from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import CurlError

from .utils import decode_bundle, decrypt, encrypt, urlsafe_base64
from rxconfig import config


logger = logging.getLogger(__name__)


class Channel(rx.Base):
    """Serializable representation of a DLHD channel."""

    id: str
    name: str
    tags: List[str]
    logo: str


class StepDaddy:
    """Wrapper around the DLHD upstream service."""

    def __init__(self) -> None:
        socks5 = config.socks5
        if socks5:
            self._session = AsyncSession(proxy=f"socks5://{socks5}")
        else:
            self._session = AsyncSession()
        self._base_url = "https://thedaddy.top"
        self.channels: List[Channel] = []
        meta_path = Path(__file__).with_name("meta.json")
        try:
            with meta_path.open("r", encoding="utf-8") as file:
                self._meta: Dict[str, Dict[str, Any]] = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load channel metadata: %s", exc)
            self._meta = {}

    async def aclose(self) -> None:
        """Close the underlying HTTP session."""
        await self._session.close()

    def _headers(self, referer: Optional[str] = None, origin: Optional[str] = None) -> Dict[str, str]:
        """Return the default headers required by the upstream service."""

        referer = referer or self._base_url
        headers = {
            "Referer": referer,
            "user-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
        }
        if origin:
            headers["Origin"] = origin
        return headers

    @staticmethod
    def _iter_channels(data: Any) -> Iterable[Dict[str, Any]]:
        """Yield channel dictionaries from the upstream schedule payload."""

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            for item in data.values():
                if isinstance(item, dict):
                    yield item

    async def load_channels(self) -> None:
        """Fetch channel metadata from the upstream service."""

        channels: Dict[str, Channel] = {}

        try:
            response = await self._session.get(
                f"{self._base_url}/24-7-channels.php", headers=self._headers()
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Failed to fetch channel listing")
        else:
            html = response.text or ""
            blocks = re.compile("<center><h1(.+?)tab-2", re.MULTILINE | re.DOTALL).findall(html)
            if blocks:
                listings = re.compile("href=\"(.*)\" target(.*)<strong>(.*)</strong>").findall(blocks[0])
                for channel_data in listings:
                    try:
                        channel = self._get_channel(channel_data)
                    except ValueError as exc:
                        logger.debug("Skipping malformed channel entry %s: %s", channel_data, exc)
                        continue
                    channels[channel.id] = channel
            else:
                logger.warning("Upstream channel listing did not contain any channels")

        try:
            schedule = await self.schedule()
        except Exception:
            logger.exception("Failed to fetch schedule while loading channels")
        else:
            for day in schedule.values():
                if not isinstance(day, dict):
                    continue
                for events in day.values():
                    if not isinstance(events, list):
                        continue
                    for event in events:
                        if not isinstance(event, dict):
                            continue
                        sources = list(self._iter_channels(event.get("channels")))
                        sources += list(self._iter_channels(event.get("channels2")))
                        for info in sources:
                            cid = str(info.get("channel_id", "")).strip()
                            if not cid:
                                continue
                            name = info.get("channel_name", cid)
                            if cid not in channels:
                                channels[cid] = self._channel_from_schedule(cid, name)

        sorted_channels = sorted(
            channels.values(),
            key=lambda channel: (channel.name.startswith("18"), channel.name, channel.id),
        )
        self._enumerate_duplicate_names(sorted_channels)
        self.channels = sorted_channels

    @staticmethod
    def _enumerate_duplicate_names(channels: List[Channel]) -> None:
        """Append numeric suffixes to channels that share a name."""

        counts: Dict[str, int] = {}
        for channel in channels:
            counts[channel.name] = counts.get(channel.name, 0) + 1

        seen: Dict[str, int] = {}
        for channel in channels:
            name = channel.name
            if counts.get(name, 0) > 1:
                index = seen.get(name, 0) + 1
                seen[name] = index
                channel.name = f"{name} ({index})"

    def _get_channel(self, channel_data: Iterable[str]) -> Channel:
        """Return a channel parsed from the site navigation."""

        data = list(channel_data)
        if len(data) < 3:
            raise ValueError(f"Channel tuple missing data: {channel_data!r}")
        slug = str(data[0])
        parts = slug.split("-")
        if len(parts) < 2:
            raise ValueError(f"Unable to parse channel id from {slug!r}")
        channel_id = parts[1].replace(".php", "").strip()
        if not channel_id:
            raise ValueError(f"Empty channel id in {channel_data!r}")

        channel_name = str(data[2])
        if channel_id == "666":
            channel_name = "Nick Music"
        if channel_id == "609":
            channel_name = "Yas TV UAE"
        if channel_name == "#0 Spain":
            channel_name = "Movistar Plus+"
        elif channel_name == "#Vamos Spain":
            channel_name = "Vamos Spain"
        clean_channel_name = re.sub(r"\s*\(.*?\)", "", channel_name)
        meta = self._meta.get(clean_channel_name, {})
        logo = meta.get("logo", "/missing.png")
        if isinstance(logo, str) and logo.startswith("http"):
            logo = f"{config.api_url}/logo/{urlsafe_base64(logo)}"
        return Channel(id=channel_id, name=channel_name, tags=meta.get("tags", []), logo=logo)

    def _channel_from_schedule(self, channel_id: str, channel_name: str) -> Channel:
        """Return a channel entry using only schedule metadata."""

        channel_id = str(channel_id).strip()
        channel_name = str(channel_name or channel_id)
        clean_channel_name = re.sub(r"\s*\(.*?\)", "", channel_name)
        meta = self._meta.get(clean_channel_name, {})
        logo = meta.get("logo", "/missing.png")
        if isinstance(logo, str) and logo.startswith("http"):
            logo = f"{config.api_url}/logo/{urlsafe_base64(logo)}"
        return Channel(id=channel_id, name=channel_name, tags=meta.get("tags", []), logo=logo)

    async def stream(self, channel_id: str) -> str:
        """Return the transformed playlist for the requested channel."""

        if not channel_id:
            raise ValueError("Channel id is required")

        key_marker = "CHANNEL_KEY"
        max_retries = 3
        prefixes = ["stream", "cast", "watch"]
        source_response = None
        source_url = ""

        for prefix in prefixes:
            url = f"{self._base_url}/{prefix}/stream-{channel_id}.php"
            if len(channel_id) > 3:
                url = f"{self._base_url}/{prefix}/bet.php?id=bet{channel_id}"
            try:
                response = await self._session.post(url, headers=self._headers())
                response.raise_for_status()
            except Exception:
                logger.debug("Failed to fetch %s stream page for %s", prefix, channel_id, exc_info=True)
                continue

            matches = re.compile("iframe src=\"(.*)\" width").findall(response.text or "")
            if not matches:
                continue
            source_url = matches[0]

            for attempt in range(1, max_retries + 1):
                try:
                    source_response = await self._session.post(
                        source_url, headers=self._headers(url)
                    )
                    source_response.raise_for_status()
                except CurlError as exc:
                    if attempt == max_retries:
                        raise
                    logger.warning(
                        "Retrying POST to %s due to %s (attempt %d/%d)",
                        source_url,
                        exc.__class__.__name__,
                        attempt,
                        max_retries,
                    )
                    continue
                except Exception as exc:
                    if attempt == max_retries:
                        raise
                    logger.warning(
                        "Retrying POST to %s due to %s (attempt %d/%d)",
                        source_url,
                        exc.__class__.__name__,
                        attempt,
                        max_retries,
                    )
                    continue
                if key_marker in (source_response.text or ""):
                    break
            if source_response and key_marker in (source_response.text or ""):
                break

        if not source_response or key_marker not in (source_response.text or ""):
            raise ValueError("Failed to find source URL for channel")

        text = source_response.text or ""
        channel_key_matches = re.compile(
            rf"const\s+{re.escape(key_marker)}\s*=\s*\"(.*?)\";"
        ).findall(text)
        if not channel_key_matches:
            raise ValueError("Channel key not found in upstream response")
        channel_key = channel_key_matches[-1]

        bundle_matches = re.compile(r"const\s+XJZ\s*=\s*\"(.*?)\";").findall(text)
        if not bundle_matches:
            raise ValueError("Bundle data missing from upstream response")
        data = decode_bundle(bundle_matches[-1])

        auth_ts = data.get("b_ts")
        auth_sig = data.get("b_sig")
        auth_rnd = data.get("b_rnd")
        auth_url = data.get("b_host")
        if not all([auth_ts, auth_sig, auth_rnd, auth_url]):
            raise ValueError("Incomplete authentication data returned from upstream")

        auth_request_url = (
            f"{auth_url}auth.php?channel_id={channel_key}&ts={auth_ts}&rnd={auth_rnd}&sig={auth_sig}"
        )
        auth_response = await self._session.get(
            auth_request_url, headers=self._headers(source_url)
        )
        auth_response.raise_for_status()

        key_url = urlparse(source_url)
        key_endpoint = f"{key_url.scheme}://{key_url.netloc}/server_lookup.php?channel_id={channel_key}"
        key_response = await self._session.get(
            key_endpoint, headers=self._headers(source_url)
        )
        key_response.raise_for_status()
        try:
            server_key = key_response.json().get("server_key")
        except (ValueError, AttributeError) as exc:
            raise ValueError("Invalid key response received from upstream") from exc
        if not server_key:
            raise ValueError("No server key found in response")

        if server_key == "top1/cdn":
            server_url = f"https://top1.newkso.ru/top1/cdn/{channel_key}/mono.m3u8"
        else:
            server_url = f"https://{server_key}new.newkso.ru/{server_key}/{channel_key}/mono.m3u8"

        m3u8 = await self._session.get(
            server_url, headers=self._headers(quote(str(source_url)))
        )
        m3u8.raise_for_status()

        return self._transform_playlist(m3u8.text or "", server_url, source_url)

    def _transform_playlist(self, playlist: str, playlist_url: str, source_url: str) -> str:
        """Rewrite playlist URLs to ensure they are reachable by the player."""

        referer_host = urlparse(source_url).netloc or urlparse(playlist_url).netloc
        encrypted_host = encrypt(referer_host) if referer_host else ""

        def absolute_url(value: str) -> str:
            if not value:
                return value
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
                return value
            return urljoin(playlist_url, value)

        def proxied_url(value: str) -> str:
            resolved = absolute_url(value)
            if config.proxy_content:
                return f"{config.api_url}/content/{encrypt(resolved)}"
            return resolved

        def replace_uri_attribute(line: str, endpoint: str | None = None) -> str:
            pattern = r'URI="(.*?)"'

            def repl(match: re.Match[str]) -> str:
                original = match.group(1)
                target = absolute_url(original)
                if endpoint == "key":
                    host = encrypted_host
                    target = f"{config.api_url}/key/{encrypt(target)}/{host}"
                elif endpoint == "content":
                    target = proxied_url(original)
                return match.group(0).replace(original, target)

            return re.sub(pattern, repl, line)

        lines: List[str] = []
        for raw_line in playlist.splitlines():
            line = raw_line
            if line.startswith("#EXT-X-KEY:") or line.startswith("#EXT-X-SESSION-KEY:"):
                line = replace_uri_attribute(line, endpoint="key")
            elif line.startswith("#EXT-X-MAP:"):
                line = replace_uri_attribute(line, endpoint="content")
            elif "URI=\"" in line:
                line = replace_uri_attribute(line, endpoint="content")
            elif line and not line.startswith("#"):
                line = proxied_url(line)
            lines.append(line)

        return "\n".join(lines) + "\n"

    async def key(self, url: str, host: str) -> bytes:
        """Fetch and return the key referenced in the playlist."""

        try:
            decrypted_url = decrypt(url)
            decrypted_host = decrypt(host)
        except Exception as exc:
            raise ValueError("Invalid key parameters") from exc

        if not decrypted_url or not decrypted_host:
            raise ValueError("Invalid key parameters")

        response = await self._session.get(
            decrypted_url,
            headers=self._headers(f"{decrypted_host}/", decrypted_host),
            timeout=60,
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def content_url(path: str) -> str:
        """Return the decrypted content URL for proxying."""

        try:
            return decrypt(path)
        except Exception as exc:
            raise ValueError("Invalid content path") from exc

    def playlist(self, channels: Optional[List[Channel]] = None) -> str:
        """Return an M3U playlist for the provided channels."""

        lines = ["#EXTM3U"]
        for channel in channels or self.channels:
            entry = (
                f" tvg-logo=\"{channel.logo}\",{channel.name}"
                if channel.logo
                else f",{channel.name}"
            )
            lines.append(f"#EXTINF:-1{entry}")
            lines.append(f"{config.api_url}/stream/{channel.id}.m3u8")
        return "\n".join(lines) + "\n"

    async def schedule(self) -> Dict[str, Any]:
        """Fetch the upstream schedule payload."""

        response = await self._session.get(
            f"{self._base_url}/schedule/schedule-generated.php",
            headers=self._headers(),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError("Invalid schedule response received") from exc
        if isinstance(data, dict):
            return data
        logger.warning("Unexpected schedule payload type: %s", type(data))
        return {}
