import importlib
import json

import pytest

from rxconfig import config


@pytest.fixture
def load_settings(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"

    def _loader(**env):
        config.api_url = "http://localhost:8000"
        monkeypatch.setenv("SETTINGS_FILE", str(settings_path))
        for key in ("API_URL", "PUBLIC_URL"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

        import dlhd_proxy.settings as settings_module

        module = importlib.reload(settings_module)
        module.apply_initial_settings()
        return module

    yield _loader
    config.api_url = "http://localhost:8000"


def test_set_public_url_persists_and_normalises(load_settings):
    settings = load_settings()

    assert settings.get_public_url() == "http://localhost:8000"

    updated = settings.set_public_url("https://example.com/tv/")
    assert updated == "https://example.com/tv"
    assert config.api_url == "https://example.com/tv"

    data = json.loads(settings.SETTINGS_FILE.read_text())
    assert data["public_url"] == "https://example.com/tv"


def test_set_public_url_rejects_invalid(load_settings):
    settings = load_settings()

    with pytest.raises(ValueError):
        settings.set_public_url("not a url")


def test_environment_override_takes_precedence(load_settings):
    settings = load_settings(PUBLIC_URL="https://proxy.test/app/")

    assert settings.get_public_url() == "https://proxy.test/app"
    assert config.api_url == "https://proxy.test/app"
    assert settings.has_env_override() is True

    updated = settings.set_public_url("https://other.example")
    assert updated == "https://proxy.test/app"
    assert config.api_url == "https://proxy.test/app"

    data = json.loads(settings.SETTINGS_FILE.read_text())
    assert data["public_url"] == "https://other.example"
