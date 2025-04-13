from typing import Annotated, Any, Literal, TypedDict

from phue2.exceptions import PhueException
from pydantic import Field
from typing_extensions import NotRequired

from fastmcp import FastMCP
from smart_home.lights.hue_utils import _get_bridge, handle_phue_error


class HueAttributes(TypedDict, total=False):
    """TypedDict for optional light attributes."""

    on: NotRequired[bool]
    bri: NotRequired[Annotated[int, Field(ge=0, le=254)]]
    hue: NotRequired[Annotated[int, Field(ge=0, le=65535)]]
    sat: NotRequired[Annotated[int, Field(ge=0, le=254)]]
    xy: NotRequired[list[float]]
    ct: NotRequired[Annotated[int, Field(ge=153, le=500)]]
    alert: NotRequired[Literal["none", "select", "lselect"]]
    effect: NotRequired[Literal["none", "colorloop"]]
    transitiontime: NotRequired[int]  # deciseconds


lights_mcp = FastMCP(
    "Hue Lights Service (phue2)",
    dependencies=[
        "smart_home@git+https://github.com/jlowin/fastmcp.git@n8example#subdirectory=examples/smart_home",
    ],
)


@lights_mcp.tool()
def read_all_lights() -> list[str]:
    """Lists the names of all available Hue lights using phue2."""
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        light_dict = bridge.get_light_objects("list")
        return [light.name for light in light_dict]
    except (PhueException, Exception) as e:
        # Simplified error handling for list return type
        return [f"Error listing lights: {e}"]


# --- Tools ---


@lights_mcp.tool()
def toggle_light(light_name: str, state: bool) -> dict[str, Any]:
    """Turns a specific light on (true) or off (false) using phue2."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}
    try:
        result = bridge.set_light(light_name, "on", state)
        return {
            "light": light_name,
            "set_on_state": state,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, Exception) as e:
        return handle_phue_error(light_name, "toggle_light", e)


@lights_mcp.tool()
def set_brightness(light_name: str, brightness: int) -> dict[str, Any]:
    """Sets the brightness of a specific light (0-254) using phue2."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}
    if not 0 <= brightness <= 254:
        # Keep specific input validation error here
        return {
            "light": light_name,
            "error": "Brightness must be between 0 and 254",
            "success": False,
        }
    try:
        result = bridge.set_light(light_name, "bri", brightness)
        return {
            "light": light_name,
            "set_brightness": brightness,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, Exception) as e:
        return handle_phue_error(light_name, "set_brightness", e)


@lights_mcp.tool()
def list_groups() -> list[str]:
    """Lists the names of all available Hue light groups."""
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        # phue2 get_group() returns a dict {id: {details}} including name
        groups = bridge.get_group()
        return [group_details["name"] for group_details in groups.values()]
    except (PhueException, Exception) as e:
        return [f"Error listing groups: {e}"]


@lights_mcp.tool()
def list_scenes() -> list[str]:
    """Lists the names of all available Hue scenes."""
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        # phue2 get_scene() returns a dict {id: {details}} including name
        scenes = bridge.get_scene()
        return [scene_details["name"] for scene_details in scenes.values()]
    except (PhueException, Exception) as e:
        return [f"Error listing scenes: {e}"]


@lights_mcp.tool()
def activate_scene(group_name: str, scene_name: str) -> dict[str, Any]:
    """Activates a specific scene within a specified light group."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}
    try:
        # Note: phue2 run_scene uses group_name and scene_name directly
        result = bridge.run_scene(group_name=group_name, scene_name=scene_name)
        # run_scene returns True on success, we'll make the response richer
        if result:
            return {
                "group": group_name,
                "activated_scene": scene_name,
                "success": True,
                "phue2_result": result,  # Include the raw True/False
            }
        else:
            # This case might indicate the scene/group exists but activation failed
            return {
                "group": group_name,
                "scene": scene_name,
                "error": "Scene activation failed (phue2 returned False)",
                "success": False,
            }

    except (KeyError, PhueException, Exception) as e:
        # KeyError likely means group or scene name is wrong
        return handle_phue_error(f"{group_name}/{scene_name}", "activate_scene", e)


@lights_mcp.tool()
def set_light_attributes(light_name: str, attributes: HueAttributes) -> dict[str, Any]:
    """Sets multiple attributes (e.g., hue, sat, bri, ct, xy, transitiontime) for a specific light."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}

    # Basic validation (more specific validation could be added)
    if not isinstance(attributes, dict) or not attributes:
        return {
            "error": "Attributes must be a non-empty dictionary",
            "success": False,
            "light": light_name,
        }

    try:
        result = bridge.set_light(light_name, attributes)
        return {
            "light": light_name,
            "set_attributes": attributes,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, ValueError, Exception) as e:
        # ValueError might occur for invalid attribute values
        return handle_phue_error(light_name, "set_light_attributes", e)


@lights_mcp.tool()
def set_group_attributes(group_name: str, attributes: HueAttributes) -> dict[str, Any]:
    """Sets multiple attributes for all lights within a specific group."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}

    if not isinstance(attributes, dict) or not attributes:
        return {
            "error": "Attributes must be a non-empty dictionary",
            "success": False,
            "group": group_name,
        }

    try:
        result = bridge.set_group(group_name, attributes)
        return {
            "group": group_name,
            "set_attributes": attributes,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, ValueError, Exception) as e:
        return handle_phue_error(group_name, "set_group_attributes", e)
