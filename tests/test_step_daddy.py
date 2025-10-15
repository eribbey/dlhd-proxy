import asyncio
import base64
import json
import re

from dlhd_proxy.step_daddy import Channel, StepDaddy
from dlhd_proxy.utils import decrypt
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


def test_transform_playlist_rewrites_relative_urls_when_proxying():
    step_daddy = StepDaddy()
    original_proxy_setting = config.proxy_content
    original_api_url = config.api_url

    try:
        config.proxy_content = True
        config.api_url = "https://proxy.test"

        playlist = "\n".join(
            [
                "#EXTM3U",
                '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"',
                "#EXT-X-MEDIA:TYPE=AUDIO,URI=\"alt-audio.m3u8\"",
                "#EXTINF:2.0,",
                "segment.ts",
                "#EXT-X-MAP:URI=\"init.mp4\"",
            ]
        )

        transformed = step_daddy._transform_playlist(
            playlist,
            "https://cdn.example.com/path/mono.m3u8",
            "https://embed.example.com/watch",
        )

        lines = transformed.splitlines()
        key_match = re.search(r'URI="([^"]+)"', lines[1])
        assert key_match is not None

        key_path = key_match.group(1).split("/key/", 1)[1].split("/")
        assert decrypt(key_path[0]) == "https://cdn.example.com/path/enc.key"
        assert decrypt(key_path[1]) == "embed.example.com"

        media_match = re.search(r'URI="([^"]+)"', lines[2])
        assert media_match is not None
        assert (
            decrypt(media_match.group(1).split("/content/", 1)[1])
            == "https://cdn.example.com/path/alt-audio.m3u8"
        )

        segment_line = lines[4]
        assert segment_line.startswith("https://proxy.test/content/")
        assert (
            decrypt(segment_line.split("/content/", 1)[1])
            == "https://cdn.example.com/path/segment.ts"
        )

        map_match = re.search(r'URI="([^"]+)"', lines[5])
        assert map_match is not None
        assert (
            decrypt(map_match.group(1).split("/content/", 1)[1])
            == "https://cdn.example.com/path/init.mp4"
        )
    finally:
        config.proxy_content = original_proxy_setting
        config.api_url = original_api_url


def test_transform_playlist_preserves_absolute_urls_without_proxy():
    step_daddy = StepDaddy()
    original_proxy_setting = config.proxy_content
    original_api_url = config.api_url

    try:
        config.proxy_content = False
        config.api_url = "https://proxy.test"

        playlist = "\n".join(
            [
                "#EXTM3U",
                '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"',
                "#EXT-X-MEDIA:TYPE=AUDIO,URI=\"alt-audio.m3u8\"",
                "#EXTINF:2.0,",
                "segment.ts",
                "variant.m3u8",
            ]
        )

        transformed = step_daddy._transform_playlist(
            playlist,
            "https://cdn.example.com/path/mono.m3u8",
            "https://embed.example.com/watch",
        )

        lines = transformed.splitlines()
        key_match = re.search(r'URI="([^"]+)"', lines[1])
        assert key_match is not None
        key_parts = key_match.group(1).split("/key/", 1)[1].split("/")
        assert decrypt(key_parts[0]) == "https://cdn.example.com/path/enc.key"
        assert decrypt(key_parts[1]) == "embed.example.com"

        media_match = re.search(r'URI="([^"]+)"', lines[2])
        assert media_match is not None
        assert media_match.group(1) == "https://cdn.example.com/path/alt-audio.m3u8"

        assert lines[4] == "https://cdn.example.com/path/segment.ts"
        assert lines[5] == "https://cdn.example.com/path/variant.m3u8"
    finally:
        config.proxy_content = original_proxy_setting
        config.api_url = original_api_url

def test_stream_retrieves_playlist_from_daddylivestream():
    async def run_test() -> None:
        step_daddy = StepDaddy()

        original_proxy_setting = config.proxy_content
        try:
            config.proxy_content = False

            channel_id = "12"
            channel_key = "channel12"
            embed_url = f"https://daddylivestream.com/embed/{channel_id}"

            bundle_payload = {
                "b_ts": base64.b64encode(b"1700000000").decode("utf-8"),
                "b_sig": base64.b64encode(b"signature").decode("utf-8"),
                "b_rnd": base64.b64encode(b"random").decode("utf-8"),
                "b_host": base64.b64encode(b"https://daddylivestream.com/").decode("utf-8"),
            }
            encoded_bundle = base64.b64encode(json.dumps(bundle_payload).encode("utf-8")).decode(
                "utf-8"
            )

            class FakeResponse:
                def __init__(self, url: str, text: str = "", status: int = 200, json_data=None):
                    self.url = url
                    self.text = text
                    self.status_code = status
                    self._json_data = json_data

                def raise_for_status(self) -> None:
                    if self.status_code >= 400:
                        raise RuntimeError(f"HTTP {self.status_code} for {self.url}")

                def json(self):
                    if self._json_data is None:
                        raise ValueError("No JSON data available")
                    return self._json_data

            class FakeSession:
                def __init__(self) -> None:
                    self.calls = []

                async def post(self, url: str, headers=None):
                    self.calls.append(("POST", url, headers or {}))
                    if url == f"https://daddylivestream.com/stream/stream-{channel_id}.php":
                        return FakeResponse(
                            url,
                            text=f'<iframe src="{embed_url}" width="640" height="360"></iframe>',
                        )
                    if url == embed_url:
                        return FakeResponse(
                            url,
                            text=(
                                f"const CHANNEL_KEY = \"{channel_key}\";\n"
                                f"const XJZ = \"{encoded_bundle}\";\n"
                            ),
                        )
                    raise AssertionError(f"Unexpected POST to {url}")

                async def get(self, url: str, headers=None):
                    self.calls.append(("GET", url, headers or {}))
                    if url.startswith("https://daddylivestream.com/auth.php"):
                        return FakeResponse(url, text="ok")
                    if url.startswith("https://daddylivestream.com/server_lookup.php"):
                        return FakeResponse(url, json_data={"server_key": "top1/cdn"})
                    if url == f"https://top1.newkso.ru/top1/cdn/{channel_key}/mono.m3u8":
                        return FakeResponse(url, text="#EXTM3U\n#EXTINF:2,\nsegment.ts\n")
                    raise AssertionError(f"Unexpected GET to {url}")

                async def close(self):
                    return None

            fake_session = FakeSession()
            real_session = step_daddy._session
            step_daddy._session = fake_session  # type: ignore[assignment]
            await real_session.close()

            playlist = await step_daddy.stream(channel_id)

            assert (
                playlist
                == "#EXTM3U\n#EXTINF:2,\nhttps://top1.newkso.ru/top1/cdn/channel12/segment.ts\n"
            )
            first_call = fake_session.calls[0]
            assert first_call[0] == "POST"
            assert first_call[1].startswith("https://daddylivestream.com/")
            assert first_call[2]["Referer"].startswith("https://daddylivestream.com")
        finally:
            config.proxy_content = original_proxy_setting
            await step_daddy.aclose()

    asyncio.run(run_test())
