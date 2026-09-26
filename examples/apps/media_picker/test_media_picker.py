from __future__ import annotations

import pytest
from media_picker_server import (
    _candidate,
    _play_via_mcp,
    _saved_ids,
    discover_media,
    mcp,
    play_media,
    save_media,
)
from prefab_ui.app import PROTOCOL_VERSION

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError


def test_discovery_normalizes_and_filters_across_sources() -> None:
    results = discover_media("ambient no music")
    assert {item["source"] for item in results} == {"youtube", "live_cam"}
    assert all(
        set(item) >= {"id", "title", "source", "source_id", "duration", "url"}
        for item in results
    )

    archive_results = discover_media("", sources=["internet_archive"])
    assert [item["id"] for item in archive_results] == ["apollo-control-room"]


async def test_actions_are_idempotent_and_reject_unknown_ids() -> None:
    _saved_ids.clear()

    play_receipt = await play_media("forest-bird-feeder-live")
    assert play_receipt["status"] == "queued"
    assert play_receipt["action"] == "play"

    assert save_media("forest-bird-feeder-live")["status"] == "saved"
    assert save_media("forest-bird-feeder-live")["status"] == "already_saved"

    with pytest.raises(ValueError, match="Unknown media id"):
        await play_media("missing")


async def test_playback_rejects_unsupported_sources_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_URL", "http://unreachable.invalid/mcp")
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_SOURCES", "youtube")

    with pytest.raises(
        ToolError, match="Internet Archive playback is not available on this device"
    ):
        await play_media("apollo-control-room")


async def test_playback_routes_normalized_candidate_through_mcp() -> None:
    actuator = FastMCP("Test actuator")

    @actuator.tool
    def play_media(source: str, source_id: str, url: str, title: str) -> dict[str, str]:
        return {
            "command": f"play:{source}:{source_id}",
            "url": url,
            "title": title,
        }

    receipt = await _play_via_mcp(
        _candidate("cold-fusion-documentary"), actuator, "play_media"
    )
    assert receipt == {
        "command": "play:youtube:jkAw87ZIwQA",
        "url": "https://www.youtube.com/watch?v=jkAw87ZIwQA",
        "title": "BobbyBroccoli — The Cold Fusion Scandal",
    }


async def test_mcp_host_loop_exposes_ui_and_marks_backend_tools_app_only() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        assert set(tools_by_name) == {
            "discover_media",
            "play_media",
            "save_media",
            "show_media_picker",
        }
        assert tools_by_name["play_media"].meta["ui"]["visibility"] == ["app"]
        assert tools_by_name["save_media"].meta["ui"]["visibility"] == ["app"]

        picker = tools_by_name["show_media_picker"]
        assert picker.meta is not None
        assert picker.meta["ui"]["resourceUri"].startswith("ui://prefab/tool/")

        result = await client.call_tool(
            "show_media_picker",
            {"candidate_ids": ["cold-fusion-documentary", "monterey-bay-live"]},
        )

    assert result.structured_content is not None
    assert result.structured_content["$prefab"]["version"] == PROTOCOL_VERSION
    assert result.structured_content["state"]["candidate_ids"] == [
        "cold-fusion-documentary",
        "monterey-bay-live",
    ]


async def test_picker_only_renders_sources_supported_by_the_actuator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_URL", "http://playback.example/mcp")
    monkeypatch.setenv("MEDIA_PICKER_ACTUATOR_SOURCES", "youtube")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "show_media_picker",
            {
                "candidate_ids": [
                    "cold-fusion-documentary",
                    "apollo-control-room",
                    "forest-bird-feeder-live",
                ]
            },
        )

    assert result.structured_content is not None
    assert result.structured_content["state"]["candidate_ids"] == [
        "cold-fusion-documentary",
        "forest-bird-feeder-live",
    ]
    assert result.structured_content["state"]["omitted_count"] == 1
