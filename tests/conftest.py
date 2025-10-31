import atexit
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_KEY_DIR = Path(tempfile.mkdtemp(prefix="dlhd-proxy-tests-"))
_KEY_PATH = _KEY_DIR / "token.key"
os.environ.setdefault("DLHD_PROXY_KEY_FILE", str(_KEY_PATH))


@atexit.register
def _cleanup_key_dir() -> None:
    try:
        if _KEY_PATH.exists():
            _KEY_PATH.unlink()
    except OSError:
        pass
    try:
        _KEY_DIR.rmdir()
    except OSError:
        pass
