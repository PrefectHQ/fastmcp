"""Hue V2 discovery, saved scenes, and native effects."""

from typing import Any, Literal

from mcp_types import ToolAnnotations
from phue import Bridge, LightState

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from smart_home.lights.hue_utils import get_bridge, hue_lifespan
from smart_home.lights.models import LightInfo, RoomState, WriteReceipt, describe_light

lights_mcp = FastMCP(
    "Hue lights",
    lifespan=hue_lifespan,
    instructions="Discover rooms, lights and scenes before changing them. Prefer IDs; names must be exact and unique. Check each bulb's supported_effects before applying a native effect. Effects run on the bulb without a polling loop. Use no_effect to stop. Changes can partially succeed before an error; read back current state to verify.",
)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


def resolve(items: dict[str, Any], target: str) -> str:
    if target in items:
        return target
    matches = [key for key, item in items.items() if item["metadata"]["name"] == target]
    if len(matches) != 1:
        raise ToolError(
            f"Target {target!r} is {'ambiguous; use an ID' if matches else 'unknown; list available targets first'}"
        )
    return matches[0]


async def resolve_room(bridge: Bridge, target: str) -> tuple[str, dict[str, Any]]:
    rooms = {room.id: room.model_dump() for room in await bridge.rooms()}
    room_id = resolve(rooms, target)
    return room_id, rooms[room_id]


async def room_members(bridge: Bridge, target: str) -> set[str]:
    _, room = await resolve_room(bridge, target)
    return {child["rid"] for child in room["children"] if child["rtype"] == "device"}


@lights_mcp.tool(annotations=READ)
async def read_lights(
    room: str | None = None, details: bool = False, bridge: Bridge = Depends(get_bridge)
) -> dict[str, LightInfo]:
    """Read lights keyed by V2 UUID, with current state and supported native effects.

    Filter by room UUID or unique exact room name. State uses brightness percent
    and kelvin; effect speed is 0–1, not amplitude. Missing values mean unknown.
    Set details=true only when the full Hue resource is needed.
    """
    members = await room_members(bridge, room) if room is not None else None
    connectivity = {
        item.model_dump().get("owner", {}).get("rid"): item.model_dump().get("status")
        for item in await bridge.resources()
        if item.type == "zigbee_connectivity"
    }
    return {
        light.id: describe_light(
            light, connectivity.get(light.owner.rid if light.owner else None), details
        )
        for light in await bridge.lights()
        if members is None or light.owner and light.owner.rid in members
    }


@lights_mcp.tool(annotations=READ)
async def read_rooms(bridge: Bridge = Depends(get_bridge)) -> dict[str, Any]:
    """Read rooms keyed by V2 UUID, including member light UUIDs and services.

    Native effects must target each member light individually. Other room-wide
    settings use set_room. Room UUIDs and member light UUIDs are distinct.
    """
    lights = await bridge.lights()
    return {
        room.id: {
            "name": room.metadata.name,
            "lights": [
                light.id
                for light in lights
                if light.owner
                and light.owner.rid in {child.rid for child in room.children}
            ],
        }
        for room in await bridge.rooms()
    }


@lights_mcp.tool(annotations=READ)
async def read_scenes(
    room: str | None = None, bridge: Bridge = Depends(get_bridge)
) -> dict[str, Any]:
    """Inspect saved scenes, including room references, palettes and per-light effects.

    Inspect actions to distinguish a static warm scene from native candle flicker.
    Filter by room UUID or unique exact name to avoid unrelated scenes. Scene
    actions retain Hue units: brightness percent, xy colors and mirek temperature.
    """
    room_id = (await resolve_room(bridge, room))[0] if room is not None else None
    return {
        scene.id: {
            "name": scene.metadata.name,
            "room_id": scene.group.rid,
            "actions": scene.actions,
            "palette": scene.palette,
            "status": scene.status,
            "speed": scene.speed,
            "auto_dynamic": scene.auto_dynamic,
        }
        for scene in await bridge.scenes()
        if room_id is None or scene.group.rid == room_id
    }


@lights_mcp.tool(annotations=WRITE)
async def set_light(
    target: str, state: LightState, bridge: Bridge = Depends(get_bridge)
) -> WriteReceipt:
    """Change one light by UUID or unique exact name, including native candle/fire effects.

    Set effect to a name advertised by the bulb; no_effect stops it. effect_speed
    is 0–1. brightness is percent, temperature_kelvin is kelvin, and
    transition_seconds controls ordinary transitions, not effect speed. xy is a
    two-number array [x, y]. Color supplied with an active effect sets its color
    parameter. Speed is not flicker amplitude; no amplitude control is exposed.
    Brightness/effects do not implicitly turn on the light; include on=true if
    desired. Acceptance is not state verification: read_lights afterward.
    """
    lights = {light.id: light.model_dump() for light in await bridge.lights()}
    light_id = resolve(lights, target)
    accepted = await bridge.set_light(light_id, state)
    return WriteReceipt(target_id=light_id, accepted=accepted)


@lights_mcp.tool(annotations=WRITE)
async def set_room(
    target: str, state: RoomState, bridge: Bridge = Depends(get_bridge)
) -> WriteReceipt:
    """Change a room by UUID or unique exact name using its grouped_light service.

    For native effects, set each member light individually after checking support.
    Mixed groups may only partially accept color settings. Use read_lights(room=...) to verify.
    """
    rooms = {room.id: room.model_dump() for room in await bridge.rooms()}
    room_id = resolve(rooms, target)
    services = [
        s["rid"] for s in rooms[room_id]["services"] if s["rtype"] == "grouped_light"
    ]
    if len(services) != 1:
        raise ToolError("Room does not expose exactly one grouped_light service")
    accepted = await bridge.set_group(services[0], state.to_light_state())
    return WriteReceipt(target_id=room_id, accepted=accepted)


@lights_mcp.tool(annotations=WRITE)
async def activate_scene(
    room: str,
    scene: str,
    action: Literal["active", "dynamic_palette", "static"] = "active",
    bridge: Bridge = Depends(get_bridge),
) -> WriteReceipt:
    """Recall a saved scene within a room, including its native per-light effects.

    active recalls the saved look; dynamic_palette requests palette cycling where
    supported. Use read_scenes to inspect what will change before recalling.
    """
    rooms = {room.id: room.model_dump() for room in await bridge.rooms()}
    room_id = resolve(rooms, room)
    scenes = {
        item.id: item.model_dump()
        for item in await bridge.scenes()
        if item.group and item.group.rid == room_id
    }
    scene_id = resolve(scenes, scene)
    accepted = await bridge.recall_scene(scene_id, action=action)
    return WriteReceipt(target_id=scene_id, accepted=accepted)
