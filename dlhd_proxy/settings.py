from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from rxconfig import config


logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("CHANNEL_DATA_DIR", "data"))
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", DATA_DIR / "settings.json"))


def _normalize_url(value: str) -> str:
    """Return ``value`` normalised for consistent comparisons."""

    raw = (value or "").strip()
    if not raw:
        return ""

    if any(ch.isspace() for ch in raw):
        raise ValueError("URL cannot contain whitespace")

    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc
    path = parsed.path.rstrip("/")
    hostname = parsed.hostname

    if not netloc or not hostname:
        raise ValueError("URL must include a hostname")
    if scheme not in {"http", "https"}:
        raise ValueError("Only HTTP or HTTPS URLs are supported")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("URL cannot include query parameters or fragments")

    if path and not path.startswith("/"):
        path = f"/{path}"

    return f"{scheme}://{netloc}{path}" if path else f"{scheme}://{netloc}"


try:
    DEFAULT_API_URL = _normalize_url(getattr(config, "api_url", "http://localhost:8000"))
except ValueError:
    DEFAULT_API_URL = "http://localhost:8000"


def _load_settings() -> Dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        logger.warning("Settings file %s contained invalid JSON", SETTINGS_FILE)
        return {}
    except OSError as exc:
        logger.warning("Unable to read settings file %s: %s", SETTINGS_FILE, exc)
        return {}
    if isinstance(data, dict):
        return data
    logger.warning("Settings file %s contained unexpected data", SETTINGS_FILE)
    return {}


def _save_settings(data: Dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def _env_public_url() -> str:
    raw = os.getenv("PUBLIC_URL") or os.getenv("API_URL") or ""
    if not raw:
        return ""
    return _normalize_url(raw)


def _stored_public_url() -> str:
    data = _load_settings()
    raw = data.get("public_url", "")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return _normalize_url(raw)


def resolve_public_url() -> str:
    """Return the active public URL using environment or stored settings."""

    for resolver, source in ((
        _env_public_url,
        "environment",
    ), (
        _stored_public_url,
        "settings file",
    )):
        try:
            url = resolver()
        except ValueError as exc:
            logger.warning("Ignoring invalid %s public URL: %s", source, exc)
            continue
        if url:
            return url
    return DEFAULT_API_URL


def apply_initial_settings() -> None:
    """Ensure ``config.api_url`` reflects stored or environment overrides."""

    config.api_url = resolve_public_url()


def get_public_url() -> str:
    """Return the current public URL used for API links."""

    return resolve_public_url()


def set_public_url(url: str) -> str:
    """Persist ``url`` and update ``config.api_url``.

    Args:
        url: The desired public URL. If empty, stored configuration is removed
            and the default (environment or bundled) value is restored.

    Returns:
        The resolved public URL after applying the update.
    """

    normalised = _normalize_url(url)
    data = _load_settings()
    if normalised:
        data["public_url"] = normalised
    else:
        data.pop("public_url", None)
    _save_settings(data)

    config.api_url = resolve_public_url()
    return config.api_url


def has_env_override() -> bool:
    """Return ``True`` if an environment variable forces the public URL."""

    return bool(os.getenv("PUBLIC_URL") or os.getenv("API_URL"))
