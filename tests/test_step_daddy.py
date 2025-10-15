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
