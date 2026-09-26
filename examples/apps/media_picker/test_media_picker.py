from __future__ import annotations

import pytest
from media_picker_server import (
    _saved_ids,
    discover_media,
    mcp,
    play_media,
    save_media,
)
from prefab_ui.app import PROTOCOL_VERSION

from fastmcp import Client


def test_discovery_normalizes_and_filters_across_sources() -> None:
    results = discover_media("ambient no music")
    assert {item["source"] for item in results} == {"youtube", "live_cam"}
    assert all(
        set(item) >= {"id", "title", "source", "duration", "url"} for item in results
    )

    archive_results = discover_media("", sources=["internet_archive"])
    assert [item["id"] for item in archive_results] == ["apollo-control-room"]


def test_actions_are_idempotent_and_reject_unknown_ids() -> None:
    _saved_ids.clear()

    play_receipt = play_media("norway-night-train")
    assert play_receipt["status"] == "queued"
    assert play_receipt["action"] == "play"

    assert save_media("norway-night-train")["status"] == "saved"
    assert save_media("norway-night-train")["status"] == "already_saved"

    with pytest.raises(ValueError, match="Unknown media id"):
        play_media("missing")


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
            {"candidate_ids": ["tokyo-rain-walk", "monterey-bay-live"]},
        )

    assert result.structured_content is not None
    assert result.structured_content["$prefab"]["version"] == PROTOCOL_VERSION
    assert result.structured_content["state"]["candidate_ids"] == [
        "tokyo-rain-walk",
        "monterey-bay-live",
    ]
