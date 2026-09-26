"""Room-level Hue state for the home view, read from a lights MCP server."""

from __future__ import annotations

import math
import os
import re
from typing import Any, TypedDict

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

HUE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_SCENES_PER_ROOM = 8


class SceneView(TypedDict):
    id: str
    name: str
    active: bool
    color: str


class RoomView(TypedDict):
    id: str
    name: str
    lights_on: int
    lights_total: int
    brightness: int
    color: str
    scenes: list[SceneView]


def checked_id(value: str) -> str:
    if HUE_ID.fullmatch(value) is None:
        raise ToolError("That room or scene is not valid.")
    return value


def lights_target() -> FastMCP | str | None:
    return os.getenv("MEDIA_PICKER_LIGHTS_URL")


async def call_lights(tool: str, arguments: dict[str, Any]) -> Any:
    target = lights_target()
    if not target:
        raise ToolError("Lights are not configured on this server.")
    async with Client(target) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    if result.is_error:
        raise ToolError("The lights did not accept that change.")
    content = result.structured_content or {}
    return content.get("result", content)


def _gamma(value: float) -> float:
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * value ** (1 / 2.4) - 0.055


def xy_to_hex(x: float, y: float) -> str:
    """Approximate sRGB for a Hue CIE xy color at full brightness."""
    if y <= 0:
        return "#ffffff"
    z = 1 - x - y
    big_x, big_y, big_z = x / y, 1.0, z / y
    rgb = (
        big_x * 1.656492 - big_y * 0.354851 - big_z * 0.255038,
        -big_x * 0.707196 + big_y * 1.655397 + big_z * 0.036152,
        big_x * 0.051713 - big_y * 0.121364 + big_z * 1.011530,
    )
    rgb = tuple(max(channel, 0.0) for channel in rgb)
    peak = max(rgb) or 1.0
    return "#" + "".join(
        f"{round(min(_gamma(channel / peak), 1.0) * 255):02x}" for channel in rgb
    )


def kelvin_to_hex(kelvin: float) -> str:
    """Approximate sRGB for a blackbody color temperature."""
    t = kelvin / 100
    red = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    green = (
        99.4708025861 * math.log(t) - 161.1195681661
        if t <= 66
        else 288.1221695283 * (t - 60) ** -0.0755148492
    )
    if t >= 66:
        blue = 255.0
    elif t <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return "#" + "".join(
        f"{round(min(max(channel, 0.0), 255.0)):02x}" for channel in (red, green, blue)
    )


def light_color(state: dict[str, Any]) -> str | None:
    if not state.get("on"):
        return None
    if state.get("temperature_kelvin"):
        return kelvin_to_hex(state["temperature_kelvin"])
    if state.get("xy"):
        x, y = state["xy"]
        return xy_to_hex(x, y)
    return None


def _xy(value: Any) -> tuple[float, float] | None:
    xy = (value or {}).get("xy") if isinstance(value, dict) else None
    if isinstance(xy, dict) and "x" in xy and "y" in xy:
        return xy["x"], xy["y"]
    return None


def scene_color(scene: dict[str, Any]) -> str:
    """The first color a scene sets, from its palette or else its light actions."""
    palette = scene.get("palette") or {}
    for entry in palette.get("color") or []:
        if xy := _xy(entry.get("color")):
            return xy_to_hex(*xy)
    for entry in palette.get("color_temperature") or []:
        mirek = (entry.get("color_temperature") or {}).get("mirek")
        if mirek:
            return kelvin_to_hex(1_000_000 / mirek)
    for action in scene.get("actions") or []:
        body = action.get("action") or {}
        if xy := _xy(body.get("color")):
            return xy_to_hex(*xy)
        mirek = (body.get("color_temperature") or {}).get("mirek")
        if mirek:
            return kelvin_to_hex(1_000_000 / mirek)
    return ""


def room_views(
    rooms: dict[str, Any], lights: dict[str, Any], scenes: dict[str, Any]
) -> list[RoomView]:
    views: list[RoomView] = []
    for room_id, room in rooms.items():
        members = [
            lights[light_id] for light_id in room["lights"] if light_id in lights
        ]
        on = [light for light in members if light["state"].get("on")]
        brightness = (
            round(sum(light["state"].get("brightness") or 0 for light in on) / len(on))
            if on
            else 0
        )
        colors = [color for light in on if (color := light_color(light["state"]))]
        room_scenes = [
            SceneView(
                id=scene_id,
                name=scene["name"],
                active=(scene.get("status") or {}).get("active", "inactive")
                != "inactive",
                color=scene_color(scene),
            )
            for scene_id, scene in scenes.items()
            if scene.get("room_id") == room_id
        ]
        room_scenes.sort(key=lambda scene: (not scene["active"], scene["name"].lower()))
        views.append(
            RoomView(
                id=room_id,
                name=room["name"],
                lights_on=len(on),
                lights_total=len(members),
                brightness=brightness,
                color=colors[0] if colors else "",
                scenes=room_scenes[:MAX_SCENES_PER_ROOM],
            )
        )
    views.sort(key=lambda room: room["name"])
    return views


async def read_home() -> list[RoomView]:
    rooms = await call_lights("read_rooms", {})
    lights = await call_lights("read_lights", {})
    scenes = await call_lights("read_scenes", {})
    return room_views(rooms, lights, scenes)
