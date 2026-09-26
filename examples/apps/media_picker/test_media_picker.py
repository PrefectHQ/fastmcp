from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx2
import media_picker_server
import pytest
from media_picker_server import (
    MediaLink,
    _auth_from_env,
    _play_via_mcp,
    _saved,
    mcp,
    play_media,
    save_media,
    verify_links,
    youtube_video_id,
)
from prefab_ui.app import PROTOCOL_VERSION

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.auth.atproto import ATProtoProvider

KNOWN_TITLES = {
    "jkAw87ZIwQA": ("The Mother of all Science Scandals", "BobbyBroccoli"),
    "D1VM6V6wmU0": ("Live Bird Feeder", "HANS Nature"),
}


@pytest.fixture
def oembed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer YouTube oEmbed for KNOWN_TITLES and 404 everything else."""
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "www.youtube.com"
        assert request.url.path == "/oembed"
        video_url = request.url.params["url"]
        requested.append(video_url)
        video_id = parse_qs(urlparse(video_url).query)["v"][0]
        if video_id not in KNOWN_TITLES:
            return httpx2.Response(404)
        title, channel = KNOWN_TITLES[video_id]
        return httpx2.Response(200, json={"title": title, "author_name": channel})

    monkeypatch.setattr(
        media_picker_server,
        "_oembed_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return requested


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=jkAw87ZIwQA", "jkAw87ZIwQA"),
        ("https://youtube.com/watch?v=jkAw87ZIwQA&t=30s", "jkAw87ZIwQA"),
        ("https://m.youtube.com/watch?v=jkAw87ZIwQA", "jkAw87ZIwQA"),
        ("https://youtu.be/jkAw87ZIwQA?si=abc", "jkAw87ZIwQA"),
        ("https://www.youtube.com/live/D1VM6V6wmU0", "D1VM6V6wmU0"),
        ("https://www.youtube.com/shorts/D1VM6V6wmU0", "D1VM6V6wmU0"),
        ("https://www.youtube.com/embed/D1VM6V6wmU0", "D1VM6V6wmU0"),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/@BobbyBroccoli", None),
        ("https://youtube.com.evil.test/watch?v=jkAw87ZIwQA", None),
        ("https://archive.org/details/apollo", None),
    ],
)
def test_youtube_video_id(url: str, expected: str | None) -> None:
    assert youtube_video_id(url) == expected


async def test_links_are_verified_with_youtube(oembed: list[str]) -> None:
    candidates, unsupported, unverified = await verify_links(
        [
            MediaLink(url="https://youtu.be/jkAw87ZIwQA", why="A deep story."),
            MediaLink(url="https://www.youtube.com/live/D1VM6V6wmU0", label="Live"),
            MediaLink(url="https://www.youtube.com/watch?v=jkAw87ZIwQA"),
            MediaLink(url="https://www.youtube.com/watch?v=aaaaaaaaaaa"),
            MediaLink(url="https://archive.org/details/apollo"),
        ]
    )

    assert [(c["source_id"], c["title"], c["channel"]) for c in candidates] == [
        ("jkAw87ZIwQA", "The Mother of all Science Scandals", "BobbyBroccoli"),
        ("D1VM6V6wmU0", "Live Bird Feeder", "HANS Nature"),
    ]
    assert candidates[0]["why"] == "A deep story."
    assert candidates[1]["label"] == "Live"
    assert candidates[0]["url"] == "https://www.youtube.com/watch?v=jkAw87ZIwQA"
    assert (unsupported, unverified) == (1, 1)
    assert len(oembed) == 4


async def test_actions_are_idempotent_and_reject_malformed_ids() -> None:
    _saved.clear()

    play_receipt = await play_media("youtube", "D1VM6V6wmU0", "Live Bird Feeder")
    assert play_receipt["status"] == "queued"
    assert play_receipt["url"] == "https://www.youtube.com/watch?v=D1VM6V6wmU0"

    assert save_media("youtube", "D1VM6V6wmU0", "Live Bird Feeder")["status"] == "saved"
    assert (
        save_media("youtube", "D1VM6V6wmU0", "Live Bird Feeder")["status"]
        == "already_saved"
    )

    with pytest.raises(ToolError, match="not valid"):
        await play_media("youtube", "x; rm -rf /", "Nope")


async def test_playback_rejects_unsupported_sources_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_URL", "http://unreachable.invalid/mcp")
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_SOURCES", "")

    with pytest.raises(ToolError, match="not available on this device"):
        await play_media("youtube", "D1VM6V6wmU0", "Live Bird Feeder")


async def test_playback_routes_verified_item_through_mcp() -> None:
    actuator = FastMCP("Test actuator")

    @actuator.tool
    def play_media(source: str, source_id: str, url: str, title: str) -> dict[str, str]:
        return {"command": f"play:{source}:{source_id}", "url": url, "title": title}

    receipt = await _play_via_mcp(
        "youtube",
        "jkAw87ZIwQA",
        "https://www.youtube.com/watch?v=jkAw87ZIwQA",
        "The Mother of all Science Scandals",
        actuator,
        "play_media",
    )
    assert receipt == {
        "command": "play:youtube:jkAw87ZIwQA",
        "url": "https://www.youtube.com/watch?v=jkAw87ZIwQA",
        "title": "The Mother of all Science Scandals",
    }


async def test_mcp_host_loop_exposes_ui_and_marks_backend_tools_app_only(
    oembed: list[str],
) -> None:
    async with Client(mcp) as client:
        tools_by_name = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools_by_name) == {"play_media", "save_media", "show_media_picker"}
        assert tools_by_name["play_media"].meta["ui"]["visibility"] == ["app"]
        assert tools_by_name["save_media"].meta["ui"]["visibility"] == ["app"]

        picker = tools_by_name["show_media_picker"]
        assert picker.meta is not None
        assert picker.meta["ui"]["resourceUri"].startswith("ui://prefab/tool/")
        link_schema = picker.input_schema["properties"]["links"]["items"]
        assert link_schema["required"] == ["url"]

        result = await client.call_tool(
            "show_media_picker",
            {
                "links": [
                    {"url": "https://youtu.be/jkAw87ZIwQA"},
                    {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
                ]
            },
        )

    assert result.structured_content is not None
    assert result.structured_content["$prefab"]["version"] == PROTOCOL_VERSION
    state = result.structured_content["state"]
    assert state["source_ids"] == ["jkAw87ZIwQA"]
    assert state["unverified_count"] == 1


async def test_picker_hides_sources_the_actuator_cannot_play(
    monkeypatch: pytest.MonkeyPatch, oembed: list[str]
) -> None:
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_URL", "http://playback.example/mcp")
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_SOURCES", "")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "show_media_picker",
            {"links": [{"url": "https://youtu.be/jkAw87ZIwQA"}]},
        )

    assert result.structured_content is not None
    assert result.structured_content["state"]["source_ids"] == []
    assert result.structured_content["state"]["unplayable_count"] == 1


def test_auth_is_off_without_a_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIA_PICKER_BASE_URL", raising=False)
    assert _auth_from_env() is None


def test_auth_requires_an_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_PICKER_BASE_URL", "https://picker.example")
    monkeypatch.setenv("MEDIA_PICKER_JWT_SIGNING_KEY", "test-secret")
    monkeypatch.delenv("MEDIA_PICKER_ALLOWED_DIDS", raising=False)
    with pytest.raises(ValueError, match="MEDIA_PICKER_ALLOWED_DIDS"):
        _auth_from_env()

    monkeypatch.setenv(
        "MEDIA_PICKER_ALLOWED_DIDS", " did:plc:abcdefghijklmnopqrstuvwx , "
    )
    provider = _auth_from_env()
    assert isinstance(provider, ATProtoProvider)
    assert "did:plc:abcdefghijklmnopqrstuvwx" in provider._allowed_dids
    assert "did:plc:zyxwvutsrqponmlkjihgfedc" not in provider._allowed_dids
