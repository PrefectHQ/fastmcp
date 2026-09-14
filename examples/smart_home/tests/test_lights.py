import json
from unittest.mock import AsyncMock

import pytest
from phue import Bridge, HueAPIError, LightState
from phue.models import Light, Room, Scene
from smart_home.hub import hub_mcp
from smart_home.lights import hue_utils

from fastmcp import Client


@pytest.fixture
def bridge(monkeypatch):
    bridge = AsyncMock(spec=Bridge)
    bridge.__aenter__.return_value = bridge
    bridge.lights.return_value = [
        Light(
            id="1",
            type="light",
            metadata={"name": "lamp"},
            owner={"rid": "d1", "rtype": "device"},
            effects_v2={
                "action": {"effect_values": ["candle", "no_effect"]},
                "status": {"effect": "candle"},
            },
        ),
        Light(
            id="2",
            type="light",
            metadata={"name": "lamp"},
            owner={"rid": "d2", "rtype": "device"},
        ),
    ]
    bridge.rooms.return_value = [
        Room(
            id="r1",
            type="room",
            metadata={"name": "living room"},
            children=[{"rid": "d1", "rtype": "device"}],
            services=[{"rid": "g1", "rtype": "grouped_light"}],
        ),
        Room(
            id="r2",
            type="room",
            metadata={"name": "bedroom"},
            children=[{"rid": "d2", "rtype": "device"}],
        ),
    ]
    bridge.scenes.return_value = [
        Scene(
            id="s1",
            type="scene",
            metadata={"name": "Candle"},
            group={"rid": "r1", "rtype": "room"},
            actions=[{"effects_v2": {"action": {"effect": "candle"}}}],
        ),
        Scene(
            id="s2",
            type="scene",
            metadata={"name": "Candle"},
            group={"rid": "r2", "rtype": "room"},
        ),
    ]
    bridge.resources.return_value = []
    bridge.set_light.return_value = []
    bridge.set_group.return_value = []
    bridge.recall_scene.return_value = []
    monkeypatch.setenv("HUE_BRIDGE_IP", "test-bridge")
    monkeypatch.setenv("HUE_BRIDGE_USERNAME", "test-user")
    monkeypatch.setattr(hue_utils, "Bridge", lambda *a, **kw: bridge)
    return bridge


async def test_native_effect_discovery_and_lifespan(bridge):
    async with Client(hub_mcp) as client:
        tools = await client.list_tools()
        assert len(tools) == 6
        assert all(
            "bridge" not in tool.input_schema.get("properties", {}) for tool in tools
        )
        lights = (await client.call_tool("hue_read_lights")).data
        assert lights["1"].supported_effects == ["candle", "no_effect"]
        assert lights["1"].state.effect == "candle"
        rooms = (await client.call_tool("hue_read_rooms")).data
        assert rooms["r1"]["lights"] == ["1"]
        scenes = (await client.call_tool("hue_read_scenes")).data
        assert scenes["s1"]["actions"][0]["effects_v2"]["action"]["effect"] == "candle"
        await client.call_tool(
            "hue_set_light",
            {"target": "1", "state": {"effect": "candle", "effect_speed": 0.5}},
        )
        bridge.set_light.assert_awaited_once_with(
            "1", LightState(effect="candle", effect_speed=0.5)
        )
        bridge.__aenter__.assert_awaited_once()
    bridge.__aexit__.assert_awaited_once()


async def test_room_and_scene_routing(bridge):
    async with Client(hub_mcp) as client:
        await client.call_tool(
            "hue_set_room", {"target": "living room", "state": {"brightness": 30}}
        )
        bridge.set_group.assert_awaited_once_with("g1", LightState(brightness=30))
        await client.call_tool(
            "hue_activate_scene", {"room": "living room", "scene": "Candle"}
        )
        bridge.recall_scene.assert_awaited_once_with("s1", action="active")
        result = await client.call_tool(
            "hue_activate_scene",
            {"room": "living room", "scene": "s2"},
            raise_on_error=False,
        )
        assert result.is_error
        assert bridge.recall_scene.await_count == 1


async def test_ambiguous_name_never_writes(bridge):
    async with Client(hub_mcp) as client:
        result = await client.call_tool(
            "hue_set_light",
            {"target": "lamp", "state": {"on": True}},
            raise_on_error=False,
        )
        assert result.is_error
        bridge.set_light.assert_not_awaited()


async def test_sdk_failure_is_tool_error(bridge):
    bridge.set_light.side_effect = HueAPIError([{"description": "unavailable"}], [])
    async with Client(hub_mcp) as client:
        result = await client.call_tool(
            "hue_set_light",
            {"target": "1", "state": {"on": True}},
            raise_on_error=False,
        )
        assert result.is_error


async def test_room_filtered_discovery_and_unknown_room(bridge):
    async with Client(hub_mcp) as client:
        lights = (
            await client.call_tool("hue_read_lights", {"room": "living room"})
        ).data
        assert set(lights) == {"1"}
        assert lights["1"].hue_details is None
        assert lights["1"].state.temperature_kelvin is None
        details = (
            await client.call_tool("hue_read_lights", {"room": "r1", "details": True})
        ).data
        assert details["1"].hue_details["owner"]["rid"] == "d1"
        scenes = (
            await client.call_tool("hue_read_scenes", {"room": "living room"})
        ).data
        assert set(scenes) == {"s1"}
        assert scenes["s1"]["room_id"] == "r1"
        result = await client.call_tool(
            "hue_read_lights", {"room": "missing"}, raise_on_error=False
        )
        assert result.is_error


async def test_control_schema_and_receipt(bridge):
    async with Client(hub_mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        room_schema = tools["hue_set_room"].input_schema
        assert '"effect"' not in json.dumps(room_schema)
        assert all(tool.annotations.open_world_hint for tool in tools.values())
        result = await client.call_tool(
            "hue_set_room",
            {"target": "r1", "state": {"effect": "candle"}},
            raise_on_error=False,
        )
        assert result.is_error
        bridge.set_group.assert_not_awaited()
        receipt = (
            await client.call_tool(
                "hue_set_light", {"target": "1", "state": {"on": True}}
            )
        ).data
        assert receipt.status == "accepted"
        assert receipt.state_verified is False
