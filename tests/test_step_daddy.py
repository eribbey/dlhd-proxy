import asyncio

from dlhd_proxy.step_daddy import Channel, StepDaddy
from dlhd_proxy.utils import urlsafe_base64
from rxconfig import config


def test_enumerate_duplicate_names():
    channels = [
        Channel(id="1", name="MLB League Pass", tags=[], logo="logo1"),
        Channel(id="2", name="MLB League Pass", tags=[], logo="logo2"),
        Channel(id="3", name="Other", tags=[], logo="logo3"),
        Channel(id="4", name="MLB League Pass", tags=[], logo="logo4"),
    ]

    StepDaddy._enumerate_duplicate_names(channels)

    assert [channel.name for channel in channels] == [
        "MLB League Pass (1)",
        "MLB League Pass (2)",
        "Other",
        "MLB League Pass (3)",
    ]


def test_load_channels_parses_stream_list(monkeypatch):
    html = """
    <div class="grid">
        <a class="card" href="/watch.php?id=149" data-title="espn sur">
            <div class="card__title">ESPN SUR</div>
            <div class="">ID: 149</div>
        </a>
        <a class="card" href="/watch.php?id=150" data-title="18+ (player-01)">
            <div class="card__title">18+ (Player-01)</div>
            <div class="">ID: 150</div>
        </a>
    </div>
    """

    class FakeResponse:
        def __init__(self, text: str, status_code: int = 200):
            self.text = text
            self.status_code = status_code

    class FakeSession:
        async def get(self, *_args, **_kwargs):
            return FakeResponse(html)

    step_daddy = StepDaddy()
    monkeypatch.setattr(step_daddy, "_session", FakeSession(), raising=False)

    step_daddy._meta = {
        "ESPN SUR": {"logo": "https://cdn.example.com/espn-sur.png", "tags": ["sports"]},
        "18+": {"logo": "https://cdn.example.com/adult.png", "tags": ["adult"]},
    }

    asyncio.run(step_daddy.load_channels())

    assert [channel.id for channel in step_daddy.channels] == ["149", "150"]

    channel_one = step_daddy.channels[0]
    assert channel_one.name == "ESPN SUR"
    assert channel_one.tags == ["sports"]
    assert channel_one.logo == (
        f"{config.api_url}/logo/{urlsafe_base64('https://cdn.example.com/espn-sur.png')}"
    )

    channel_two = step_daddy.channels[1]
    assert channel_two.name == "18+ (Player-01)"
    assert channel_two.tags == ["adult"]
    assert channel_two.logo == (
        f"{config.api_url}/logo/{urlsafe_base64('https://cdn.example.com/adult.png')}"
    )


def test_load_channels_logs_request_status(monkeypatch, caplog):
    html = """
    <div class="grid">
        <a class="card" href="/watch.php?id=149">
            <div class="card__title">ESPN SUR</div>
        </a>
    </div>
    """

    class FakeResponse:
        def __init__(self, text: str, status_code: int = 200):
            self.text = text
            self.status_code = status_code

    class FakeSession:
        async def get(self, *_args, **_kwargs):
            return FakeResponse(html)

    step_daddy = StepDaddy()
    monkeypatch.setattr(step_daddy, "_session", FakeSession(), raising=False)

    caplog.set_level("INFO")
    asyncio.run(step_daddy.load_channels())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Request to https://daddylivestream.com/24-7-channels.php succeeded with HTTP 200"
        in message
        for message in messages
    )


def test_stream_proxies_ts_segments_but_not_php(monkeypatch):
    class FakeResponse:
        def __init__(self, text: str = "", status_code: int = 200, json_data=None, content: bytes = b""):
            self.text = text
            self.status_code = status_code
            self._json_data = json_data
            self.content = content

        def json(self):
            return self._json_data

    iframe_html = '<iframe src="https://example.com/player.html" width="600"></iframe>'
    m3u8_text = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI=\"https://example.com/key.key\"
#EXTINF:4.0,
https://cdn.example.com/video1.ts
#EXTINF:8.0,
https://cdn.example.com/variant.m3u8
#EXTINF:1.0,
https://cdn.example.com/thumbnail.png
#EXTINF:2.0,
https://api.example.com/segment.php?id=1
"""

    class FakeResponse:
        def __init__(self, text: str = "", status_code: int = 200, json_data=None):
            self.text = text
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    responses = iter(
        [
            FakeResponse(text=iframe_html),
            FakeResponse(text='const CHANNEL_KEY = "abc123";'),
            FakeResponse(text="ok", status_code=200),
            FakeResponse(json_data={"server_key": "edge1/"}),
            FakeResponse(text=m3u8_text),
        ]
    )

    async def fake_get(_self, url: str, **_kwargs):
        try:
            return next(responses)
        except StopIteration:  # pragma: no cover - unexpected extra request
            raise AssertionError(f"Unexpected request to {url}")

    step_daddy = StepDaddy()
    monkeypatch.setattr("dlhd_proxy.step_daddy.decode_bundle", lambda _text: {
        "b_ts": "123",
        "b_sig": "abc",
        "b_rnd": "rnd",
        "b_host": "https://auth.example.com/",
    })
    monkeypatch.setattr("dlhd_proxy.step_daddy.encrypt", lambda value: f"enc({value})")
    monkeypatch.setattr(config, "proxy_content", True, raising=False)
    monkeypatch.setattr(step_daddy, "_get", fake_get.__get__(step_daddy, StepDaddy))

    playlist = asyncio.run(step_daddy.stream("42"))

    ts_line = f"{config.api_url}/content/enc(https://cdn.example.com/video1.ts)"
    m3u8_line = f"{config.api_url}/content/enc(https://cdn.example.com/variant.m3u8)"
    assert ts_line in playlist
    assert m3u8_line in playlist
    assert "https://cdn.example.com/thumbnail.png" not in playlist
    assert "https://api.example.com/segment.php?id=1" not in playlist
    assert "#EXTINF:1.0," not in playlist
    assert "#EXTINF:2.0," not in playlist


def test_stream_proxies_hls_when_proxy_disabled(monkeypatch):
    iframe_html = '<iframe src="https://example.com/embed" width="100%" height="100%"></iframe>'
    m3u8_text = """#EXTM3U
#EXTINF:4.0,
https://cdn.example.com/video1.ts
#EXTINF:8.0,
https://cdn.example.com/variant.m3u8
#EXTINF:1.0,
https://cdn.example.com/thumbnail.png
"""

    class FakeResponse:
        def __init__(self, text: str = "", status_code: int = 200, json_data=None):
            self.text = text
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    responses = iter(
        [
            FakeResponse(text=iframe_html),
            FakeResponse(text='const CHANNEL_KEY = "abc123";'),
            FakeResponse(text="ok", status_code=200),
            FakeResponse(json_data={"server_key": "edge1/"}),
            FakeResponse(text=m3u8_text),
        ]
    )

    async def fake_get(_self, url: str, **_kwargs):
        try:
            return next(responses)
        except StopIteration:  # pragma: no cover - unexpected extra request
            raise AssertionError(f"Unexpected request to {url}")

    step_daddy = StepDaddy()
    monkeypatch.setattr("dlhd_proxy.step_daddy.decode_bundle", lambda _text: {
        "b_ts": "123",
        "b_sig": "abc",
        "b_rnd": "rnd",
        "b_host": "https://auth.example.com/",
    })
    monkeypatch.setattr("dlhd_proxy.step_daddy.encrypt", lambda value: f"enc({value})")
    monkeypatch.setattr(config, "proxy_content", False, raising=False)
    monkeypatch.setattr(step_daddy, "_get", fake_get.__get__(step_daddy, StepDaddy))

    playlist = asyncio.run(step_daddy.stream("42"))

    ts_line = f"{config.api_url}/content/enc(https://cdn.example.com/video1.ts)"
    m3u8_line = f"{config.api_url}/content/enc(https://cdn.example.com/variant.m3u8)"
    assert ts_line in playlist
    assert m3u8_line in playlist
    assert "https://cdn.example.com/thumbnail.png" not in playlist
    assert "#EXTINF:1.0," not in playlist
