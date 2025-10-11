import reflex as rx
import os


proxy_content = os.environ.get("PROXY_CONTENT", "TRUE").upper() == "TRUE"
socks5 = os.environ.get("SOCKS5", "")
timezone = os.environ.get("TZ", "UTC")
guide_update = os.environ.get("GUIDE_UPDATE", "03:00")
env_public_url = os.environ.get("PUBLIC_URL") or os.environ.get("API_URL")

print(
    f"PROXY_CONTENT: {proxy_content}\nSOCKS5: {socks5}\nTZ: {timezone}"
    f"\nGUIDE_UPDATE: {guide_update}\nPUBLIC_URL: {(env_public_url or '').rstrip('/') or 'default'}"
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

default_api_url = getattr(config, "api_url", "http://localhost:8000")
if env_public_url:
    config.api_url = env_public_url.rstrip("/")
else:
    config.api_url = default_api_url.rstrip("/")
