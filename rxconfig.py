import os
from urllib.parse import urlparse

import reflex as rx


proxy_content = os.environ.get("PROXY_CONTENT", "TRUE").upper() == "TRUE"
socks5 = os.environ.get("SOCKS5", "")
timezone = os.environ.get("TZ", "UTC")
guide_update = os.environ.get("GUIDE_UPDATE", "03:00")

flaresolverr_url = os.environ.get("FLARESOLVERR_URL", "").strip()
if flaresolverr_url:
    parsed = urlparse(flaresolverr_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("FLARESOLVERR_URL must include a valid http(s) scheme and host")

try:
    flaresolverr_timeout = int(os.environ.get("FLARESOLVERR_TIMEOUT", "60"))
except ValueError as exc:
    raise ValueError("FLARESOLVERR_TIMEOUT must be an integer number of seconds") from exc

if flaresolverr_timeout <= 0:
    raise ValueError("FLARESOLVERR_TIMEOUT must be greater than zero")

print(
    f"PROXY_CONTENT: {proxy_content}\n"
    f"SOCKS5: {socks5}\n"
    f"TZ: {timezone}\n"
    f"GUIDE_UPDATE: {guide_update}\n"
    f"FLARESOLVERR_URL: {flaresolverr_url or '(disabled)'}\n"
    f"FLARESOLVERR_TIMEOUT: {flaresolverr_timeout}"
)

config = rx.Config(
    app_name="dlhd_proxy",
    proxy_content=proxy_content,
    socks5=socks5,
    show_built_with_reflex=False,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ],
)

config.timezone = timezone
config.guide_update = guide_update
config.flaresolverr_url = flaresolverr_url
config.flaresolverr_timeout = flaresolverr_timeout
