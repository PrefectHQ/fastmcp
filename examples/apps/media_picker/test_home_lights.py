from __future__ import annotations

import json
from typing import Any

import home_lights
import pytest
from home_lights import kelvin_to_hex, room_views, scene_color, xy_to_hex
from media_picker_server import mcp

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

BEDROOM = "11111111-1111-4111-8111-111111111111"
OFFICE = "22222222-2222-4222-8222-222222222222"
LAMP = "33333333-3333-4333-8333-333333333333"
DESK = "44444444-4444-4444-8444-444444444444"
CANDLE = "55555555-5555-4555-8555-555555555555"
BRIGHT = "66666666-6666-4666-8666-666666666666"

ROOMS = {
    BEDROOM: {"name": "bedroom", "lights": [LAMP]},
    OFFICE: {"name": "office", "lights": [DESK]},
}
LIGHTS = {
    LAMP: {
        "name": "lamp",
        "state": {"on": True, "brightness": 30.0, "xy": [0.5, 0.41]},
    },
    DESK: {"name": "desk", "state": {"on": False, "brightness": 80.0}},
}
SCENES = {
    BRIGHT: {"name": "Bright", "room_id": BEDROOM, "status": {"active": "inactive"}},
    CANDLE: {"name": "candle", "room_id": BEDROOM, "status": {"active": "static"}},
}


@pytest.fixture
def hue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    writes: list[tuple[str, dict[str, Any]]] = []
    server = FastMCP("Fake Hue")

    @server.tool
    def read_rooms() -> dict[str, Any]:
        return ROOMS

    @server.tool
    def read_lights() -> dict[str, Any]:
        return LIGHTS

    @server.tool
    def read_scenes() -> dict[str, Any]:
        return SCENES

    @server.tool
    def set_room(target: str, state: dict[str, Any]) -> dict[str, Any]:
        writes.append(("set_room", {"target": target, "state": state}))
        return {"target_id": target, "accepted": True}

    @server.tool
    def activate_scene(room: str, scene: str) -> dict[str, Any]:
        writes.append(("activate_scene", {"room": room, "scene": scene}))
        return {"target_id": scene, "accepted": True}

    monkeypatch.setattr(home_lights, "lights_target", lambda: server)
    return writes


def test_room_views_summarize_power_brightness_color_and_active_scene() -> None:
    views = room_views(ROOMS, LIGHTS, SCENES)
    bedroom, office = views
    assert (bedroom["name"], bedroom["lights_on"], bedroom["brightness"]) == (
        "bedroom",
        1,
        30,
    )
    assert bedroom["color"].startswith("#") and len(bedroom["color"]) == 7
    assert [scene["name"] for scene in bedroom["scenes"]] == ["candle", "Bright"]
    assert bedroom["scenes"][0]["active"] is True
    assert (office["lights_on"], office["brightness"], office["color"]) == (0, 0, "")


@pytest.mark.parametrize(
    ("color", "check"),
    [
        (xy_to_hex(0.5, 0.41), lambda rgb: rgb[0] > rgb[2]),
        (kelvin_to_hex(2200), lambda rgb: rgb[0] > rgb[2]),
        (kelvin_to_hex(6500), lambda rgb: abs(rgb[0] - rgb[2]) < 40),
    ],
)
def test_colors_are_plausible_hex(color: str, check: Any) -> None:
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    assert check(rgb)


async def test_home_view_renders_rooms(hue: list[Any]) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("show_home", {})
    assert result.structured_content is not None
    assert result.structured_content["state"]["room_ids"] == [BEDROOM, OFFICE]
    rendered = json.dumps(result.structured_content)
    assert '"Switch"' in rendered and "{{ $event }}" in rendered
    assert hue == []


async def test_light_tools_forward_validated_commands(hue: list[Any]) -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        for name in ("set_room_power", "set_room_brightness", "activate_room_scene"):
            assert tools[name].meta["ui"]["visibility"] == ["app"]

        power = await client.call_tool(
            "set_room_power", {"room_id": OFFICE, "room_name": "office", "on": True}
        )
        level = await client.call_tool(
            "set_room_brightness",
            {"room_id": BEDROOM, "room_name": "bedroom", "brightness": 40},
        )
        scene = await client.call_tool(
            "activate_room_scene",
            {"room_id": BEDROOM, "scene_id": CANDLE, "scene_name": "candle"},
        )

    assert [power.data["message"], level.data["message"], scene.data["message"]] == [
        "office on.",
        "bedroom at 40%.",
        "candle on.",
    ]
    assert hue == [
        ("set_room", {"target": OFFICE, "state": {"on": True}}),
        (
            "set_room",
            {
                "target": BEDROOM,
                "state": {"on": True, "brightness": 40, "transition_seconds": 0.4},
            },
        ),
        ("activate_scene", {"room": BEDROOM, "scene": CANDLE}),
    ]


async def test_light_tools_reject_bad_input_before_dispatch(hue: list[Any]) -> None:
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not valid"):
            await client.call_tool(
                "set_room_power", {"room_id": "office", "room_name": "x", "on": True}
            )
        with pytest.raises(ToolError, match="between 1 and 100"):
            await client.call_tool(
                "set_room_brightness",
                {"room_id": BEDROOM, "room_name": "bedroom", "brightness": 0},
            )
    assert hue == []


async def test_home_view_without_lights_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(home_lights, "lights_target", lambda: None)
    async with Client(mcp) as client:
        result = await client.call_tool("show_home", {})
    assert result.structured_content is not None
    assert result.structured_content["state"]["room_ids"] == []


@pytest.mark.parametrize(
    ("scene", "expected"),
    [
        ({"palette": {"color": [{"color": {"xy": {"x": 0.5, "y": 0.41}}}]}}, "xy"),
        (
            {"palette": {"color_temperature": [{"color_temperature": {"mirek": 454}}]}},
            "kelvin",
        ),
        (
            {"actions": [{"action": {"color": {"xy": {"x": 0.15, "y": 0.06}}}}]},
            "xy",
        ),
        ({"palette": {}, "actions": [{"action": {"on": {"on": True}}}]}, ""),
    ],
)
def test_scene_color_prefers_palette_then_actions(
    scene: dict[str, Any], expected: str
) -> None:
    color = scene_color(scene)
    if expected == "":
        assert color == ""
    else:
        assert color.startswith("#") and len(color) == 7


async def test_home_view_allows_youtube_thumbnails(hue: list[Any]) -> None:
    async with Client(mcp) as client:
        resources = await client.list_resources()
        renderers = [r for r in resources if "prefab/tool" in str(r.uri)]
        domains = {
            domain
            for renderer in renderers
            for domain in (renderer.meta or {})
            .get("ui", {})
            .get("csp", {})
            .get("resourceDomains", [])
        }
    assert "https://i.ytimg.com" in domains
