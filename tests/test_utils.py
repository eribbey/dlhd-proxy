import base64
import importlib
import json

import pytest

from dlhd_proxy import utils


@pytest.fixture(autouse=True)
def reload_utils_with_temp_key(tmp_path, monkeypatch):
    key_path = tmp_path / "token.key"
    monkeypatch.setenv("DLHD_PROXY_KEY_FILE", str(key_path))
    importlib.reload(utils)
    yield


@pytest.mark.parametrize(
    "value",
    ["https://example.com", "simple-string", "12345"],
)
def test_encrypt_decrypt_round_trip(value: str) -> None:
    encoded = utils.encrypt(value)
    assert encoded != value
    assert utils.decrypt(encoded) == value


def test_urlsafe_base64_helpers() -> None:
    original = "hello world"
    encoded = utils.urlsafe_base64(original)
    # Ensure Python's decoder accepts the result and our helper reverses it.
    decoded_bytes = base64.urlsafe_b64decode(encoded + "=")
    assert decoded_bytes == original.encode()
    assert utils.urlsafe_base64_decode(encoded) == original


def test_decode_bundle_handles_nested_strings() -> None:
    bundle_data = {
        "b_ts": base64.b64encode(b"123456").decode(),
        "b_sig": "not-base64",
        "nested": {"inner": "value"},
    }
    encoded_bundle = base64.b64encode(json.dumps(bundle_data).encode()).decode()
    decoded = utils.decode_bundle(encoded_bundle)
    assert decoded["b_ts"] == "123456"
    assert decoded["b_sig"] == "not-base64"
    assert decoded["nested"] == bundle_data["nested"]


def test_extract_and_decode_var_success() -> None:
    secret = base64.b64encode(b"abc").decode()
    response = f"var SECRET = atob(\"{secret}\");"
    assert utils.extract_and_decode_var("SECRET", response) == "abc"


def test_extract_and_decode_var_missing() -> None:
    with pytest.raises(ValueError):
        utils.extract_and_decode_var("MISSING", "var OTHER = atob('aGVsbG8=');")


@pytest.mark.parametrize("invalid", ["@@@", "==="])
def test_decrypt_invalid_base64(invalid: str) -> None:
    with pytest.raises(ValueError):
        utils.decrypt(invalid)


def test_token_key_file_is_created(tmp_path, monkeypatch) -> None:
    key_path = tmp_path / "custom.key"
    monkeypatch.setenv("DLHD_PROXY_KEY_FILE", str(key_path))
    module = importlib.reload(utils)
    assert key_path.exists()
    assert key_path.read_bytes() == module.key_bytes


def test_token_key_file_reuse(tmp_path, monkeypatch) -> None:
    key_path = tmp_path / "persist.key"
    monkeypatch.setenv("DLHD_PROXY_KEY_FILE", str(key_path))
    module = importlib.reload(utils)
    token = module.encrypt("hello-world")
    key_bytes = key_path.read_bytes()

    module = importlib.reload(utils)
    assert key_path.read_bytes() == key_bytes
    assert module.decrypt(token) == "hello-world"


def test_short_token_key_file_rejected(tmp_path, monkeypatch) -> None:
    key_path = tmp_path / "short.key"
    key_path.write_bytes(b"too-short")
    monkeypatch.setenv("DLHD_PROXY_KEY_FILE", str(key_path))
    with pytest.raises(RuntimeError):
        importlib.reload(utils)
