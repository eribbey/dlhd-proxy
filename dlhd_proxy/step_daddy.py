import html
import logging
import re
import json
import time
import email.utils
from http.cookies import SimpleCookie
from importlib import resources
from pathlib import Path
from typing import Iterable, List
from urllib.parse import parse_qs, quote, urljoin, urlparse, urlsplit

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


class _FlaresolverrResponse:
    def __init__(self, solution: dict):
        self.status_code = int(solution.get("status") or 0)
        self.text = solution.get("response") or ""
        self.content = self.text.encode()
        self.headers = solution.get("headers") or {}
        self.url = solution.get("url") or ""

    def json(self):
        return json.loads(self.text)


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
        self._base_url = "https://dlhd.dad"
        self._flaresolverr_url = config.flaresolverr_url
        self._flaresolverr_timeout = config.flaresolverr_timeout
        self.channels: list[Channel] = []
        try:
            meta_data = resources.files(__package__).joinpath("meta.json").read_text()
            self._meta = json.loads(meta_data)
        except Exception:
            self._meta = {}
        self._logged_domains = {"dlhd.dad"}
        self._last_transport_mode: bool | None = None

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
            logger.info("Loaded %d channels from dlhd.dad", len(channels))
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
        logger.info("Resolved channel %s to source %s with key %s", channel_id, source_url, channel_key)

        data = decode_bundle(source_response.text)
        auth_ts = data.get("b_ts", "")
        auth_sig = data.get("b_sig", "")
        auth_rnd = data.get("b_rnd", "")
        raw_auth_url = data.get("b_host", "")
        auth_url = re.sub(r"\s+", "", raw_auth_url.strip())
        parsed_auth_url = urlparse(auth_url)
        if not parsed_auth_url.scheme or not parsed_auth_url.netloc:
            raise ValueError(
                f"Invalid auth host {raw_auth_url!r}: missing scheme or hostname"
            )

        if ":" in parsed_auth_url.netloc:
            host, _, port_str = parsed_auth_url.netloc.rpartition(":")
            if not port_str.isdigit():
                logger.warning(
                    "Auth host %r contained a non-numeric port; stripping port and retrying",
                    raw_auth_url,
                )
                parsed_auth_url = parsed_auth_url._replace(netloc=host)

        try:
            parsed_auth_url.port
        except ValueError as exc:
            raise ValueError(
                f"Invalid auth host {raw_auth_url!r}: port must be numeric"
            ) from exc

        if ":" in parsed_auth_url.netloc and parsed_auth_url.port is None:
            raise ValueError(
                f"Invalid auth host {raw_auth_url!r}: missing port number"
            )
        auth_base = parsed_auth_url._replace(path=parsed_auth_url.path or "/").geturl()
        auth_request_url = urljoin(
            auth_base,
            f"auth.php?channel_id={channel_key}&ts={auth_ts}&rnd={auth_rnd}&sig={auth_sig}",
        )
        logger.debug(
            "Requesting auth for channel %s from %s", channel_id, auth_request_url
        )
        auth_response = await self._get(
            auth_request_url, headers=self._headers(source_url)
        )
        if auth_response.status_code != 200:
            raise ValueError("Failed to get auth response")
        key_url = urlparse(source_url)
        key_url = f"{key_url.scheme}://{key_url.netloc}/server_lookup.php?channel_id={channel_key}"
        logger.debug("Fetching server key for channel %s from %s", channel_id, key_url)
        key_response = await self._get(key_url, headers=self._headers(source_url))
        raw_server_key = key_response.json().get("server_key")
        if not raw_server_key:
            raise ValueError("No server key found in response")
        cleaned_server_key = raw_server_key.strip()
        if ":" in cleaned_server_key:
            raise ValueError(
                f"Invalid server key {raw_server_key!r}: unexpected characters in hostname"
            )
        server_key = cleaned_server_key.strip(" /")
        if not server_key:
            raise ValueError(f"Invalid server key {raw_server_key!r}: missing scheme or hostname")
        if server_key == "top1/cdn":
            server_base = "https://top1.newkso.ru/"
            server_url = urljoin(server_base, f"{server_key}/{channel_key}/mono.m3u8")
        else:
            server_base = f"https://{server_key}new.newkso.ru/"
            parsed_server_base = urlparse(server_base)
            if not parsed_server_base.scheme or not parsed_server_base.netloc:
                raise ValueError(
                    f"Invalid server key {raw_server_key!r}: missing scheme or hostname"
                )
            server_url = urljoin(
                parsed_server_base.geturl(), f"{server_key}/{channel_key}/mono.m3u8"
            )
        m3u8 = await self._get(
            server_url, headers=self._headers(quote(str(source_url)))
        )
        logger.info(
            "Retrieved playlist for channel %s from %s (auth host %s)",
            channel_id,
            server_url,
            parsed_auth_url.netloc,
        )
        rewritten_lines: list[str] = []
        for line in m3u8.text.splitlines():
            if line.startswith("#EXT-X-KEY:"):
                original_url = re.search(r'URI="(.*?)"', line).group(1)
                line = line.replace(
                    original_url,
                    f"{config.api_url}/key/{encrypt(original_url)}/{encrypt(urlparse(source_url).netloc)}",
                )
                rewritten_lines.append(line)
                continue

            if line.startswith("http"):
                parsed_url = urlparse(line)
                path = (parsed_url.path or "").lower()
                if _is_hls_path(path):
                    rewritten_lines.append(f"{config.api_url}/content/{encrypt(line)}")
                elif config.proxy_content:
                    rewritten_lines.append(f"{config.api_url}/content/{encrypt(line)}")
                else:
                    rewritten_lines.append(line)
                continue

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
        for path in ("/schedule", "/"):
            try:
                html_response = await self._get(
                    f"{self._base_url}{path}", headers=self._headers()
                )
            except Exception as exc:  # pragma: no cover - network failure
                logger.debug("Schedule request to %s failed: %s", path, exc)
                continue
            if html_response.status_code >= 400:
                logger.debug(
                    "Schedule request %s returned HTTP %s",
                    path,
                    html_response.status_code,
                )
                continue
            try:
                schedule = self._parse_schedule_html(html_response.text)
            except ValueError as exc:
                logger.debug("Unable to parse schedule HTML from %s: %s", path, exc)
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
        use_flaresolverr = self._should_use_flaresolverr(url)
        transport = " via Flaresolverr" if use_flaresolverr else ""
        try:
            if use_flaresolverr:
                response = await self._flaresolverr_get(url, **kwargs)
            else:
                response = await self._session.get(url, **kwargs)
        except Exception:
            if self._should_log_url(url):
                logger.exception("Request to %s%s failed", url, transport)
            raise

        if (
            not use_flaresolverr
            and response.status_code in {401, 403}
            and self._can_use_flaresolverr(url)
        ):
            if self._should_log_url(url):
                logger.info(
                    "Switching transport to Flaresolverr for %s after HTTP %s",
                    url,
                    response.status_code,
                )
            use_flaresolverr = True
            transport = " via Flaresolverr"
            response = await self._flaresolverr_get(url, **kwargs)

        self._log_transport_change(use_flaresolverr, url)
        if self._should_log_url(url):
            if response.status_code >= 400:
                logger.warning(
                    "Request to %s%s returned HTTP %s",
                    url,
                    transport,
                    response.status_code,
                )
            else:
                logger.info(
                    "Request to %s%s succeeded with HTTP %s",
                    url,
                    transport,
                    response.status_code,
                )
        return response

    def _should_use_flaresolverr(self, url: str) -> bool:
        if not self._can_use_flaresolverr(url):
            return False

        hostname = (urlsplit(url).hostname or "").lower()
        return not self._has_valid_cookie(hostname)

    def _can_use_flaresolverr(self, url: str) -> bool:
        hostname = (urlsplit(url).hostname or "").lower()
        flaresolverr_url = self._flaresolverr_url or config.flaresolverr_url
        return bool(flaresolverr_url) and hostname.endswith("dlhd.dad")

    async def _flaresolverr_get(self, url: str, headers=None, timeout: int | None = None, **_kwargs):
        flaresolverr_url = self._flaresolverr_url or config.flaresolverr_url
        if not flaresolverr_url:
            raise ValueError("Flaresolverr is not configured")

        self._flaresolverr_url = flaresolverr_url
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int((timeout or self._flaresolverr_timeout) * 1000),
        }
        if headers:
            payload["headers"] = headers

        try:
            response = await self._session.post(
                flaresolverr_url,
                json=payload,
                timeout=timeout or self._flaresolverr_timeout,
            )
        except Exception:
            logger.exception("Flaresolverr request to %s failed", url)
            raise

        if response.status_code >= 400:
            raise ValueError(
                f"Flaresolverr request failed with HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise ValueError("Invalid Flaresolverr response") from exc

        if payload.get("status") != "ok":
            message = payload.get("message") or "Unknown Flaresolverr error"
            raise ValueError(message)

        solution = payload.get("solution") or {}
        self._store_solution_cookies(url, solution.get("headers") or {})
        return _FlaresolverrResponse(solution)

    def _store_solution_cookies(self, url: str, headers: dict) -> None:
        cookies_raw: list[str] = []
        for key, value in headers.items():
            if key.lower() != "set-cookie":
                continue

            if isinstance(value, list):
                cookies_raw.extend([str(item) for item in value])
            else:
                cookies_raw.append(str(value))

        if not cookies_raw:
            return

        hostname = (urlsplit(url).hostname or "dlhd.dad").lower()
        now = time.time()

        for raw_cookie in cookies_raw:
            parsed = SimpleCookie()
            parsed.load(raw_cookie)
            for morsel in parsed.values():
                expires = None
                if morsel["max-age"]:
                    try:
                        expires = now + int(morsel["max-age"])
                    except (TypeError, ValueError):  # pragma: no cover - defensive
                        expires = None
                elif morsel["expires"]:
                    try:
                        expires_dt = email.utils.parsedate_to_datetime(morsel["expires"])
                        expires = expires_dt.timestamp()
                    except (TypeError, ValueError, OverflowError):  # pragma: no cover
                        expires = None

                domain = morsel["domain"] or hostname
                path = morsel["path"] or "/"
                self._session.cookies.set(
                    morsel.key, morsel.value, domain=domain, path=path, expires=expires
                )

    def _has_valid_cookie(self, hostname: str) -> bool:
        now = time.time()
        cookies = getattr(self._session, "cookies", None)
        if cookies is None:  # pragma: no cover - defensive
            return False

        for cookie in cookies:
            domain = (cookie.domain or "").lstrip(".").lower()
            if domain and not hostname.endswith(domain):
                continue

            if cookie.expires is None or cookie.expires > now:
                return True

        return False

    def _log_transport_change(self, using_flaresolverr: bool, url: str) -> None:
        previous_mode = self._last_transport_mode
        self._last_transport_mode = using_flaresolverr

        if previous_mode is None or previous_mode == using_flaresolverr:
            return

        if not self._should_log_url(url):  # pragma: no cover - logging scope guard
            return

        mode_label = "Flaresolverr" if using_flaresolverr else "direct"
        netloc = urlsplit(url).netloc
        logger.info("Transport for %s switched to %s", netloc, mode_label)

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
