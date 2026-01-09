# /// script
# dependencies = [
#     "smart_home@git+https://github.com/jlowin/fastmcp.git#subdirectory=examples/smart_home",
#     "fastmcp",
# ]
# ///

from typing import Annotated, Any, Literal, TypedDict

from mcp.types import ToolAnnotations
from phue2.exceptions import PhueException
from pydantic import Field
from typing_extensions import NotRequired

from fastmcp import FastMCP
from smart_home.lights.hue_utils import _get_bridge, handle_phue_error


class HueAttributes(TypedDict, total=False):
    """TypedDict for optional light attributes."""

    on: NotRequired[Annotated[bool, Field(description="on/off state")]]
    bri: NotRequired[Annotated[int, Field(ge=0, le=254, description="brightness")]]
    hue: NotRequired[
        Annotated[
            int,
            Field(
                ge=0,
                le=65535,
                description="hue (color wheel position)",
            ),
        ]
    ]
    sat: NotRequired[
        Annotated[
            int,
            Field(
                ge=0,
                le=254,
                description="saturation",
            ),
        ]
    ]
    xy: NotRequired[Annotated[list[float], Field(description="xy color coordinates")]]
    ct: NotRequired[
        Annotated[
            int,
            Field(ge=153, le=500, description="color temperature"),
        ]
    ]
    alert: NotRequired[Literal["none", "select", "lselect"]]
    effect: NotRequired[Literal["none", "colorloop"]]
    transitiontime: NotRequired[Annotated[int, Field(description="deciseconds")]]


# Dependencies are configured in lights.fastmcp.json
lights_mcp = FastMCP("Hue Lights Service (phue2)")


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
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


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
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


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
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


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
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


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def list_scenes() -> dict[str, list[str]] | list[str]:
    """Lists Hue scenes, grouped by the light group they belong to.

    Returns:
        dict[str, list[str]]: A dictionary mapping group names to a list of scene names within that group.
        list[str]: An error message list if the bridge connection fails or an error occurs.
    """
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        scenes_data = bridge.get_scene()  # Returns dict {scene_id: {details...}}
        groups_data = bridge.get_group()  # Returns dict {group_id: {details...}}

        # Create a lookup for group name by group ID
        group_id_to_name = {gid: ginfo["name"] for gid, ginfo in groups_data.items()}

        scenes_by_group: dict[str, list[str]] = {}
        for scene_id, scene_details in scenes_data.items():
            scene_name = scene_details.get("name")
            # Scenes might be associated with a group via 'group' key or lights
            # Using 'group' key if available is more direct for group scenes
            group_id = scene_details.get("group")
            if scene_name and group_id and group_id in group_id_to_name:
                group_name = group_id_to_name[group_id]
                if group_name not in scenes_by_group:
                    scenes_by_group[group_name] = []
                # Avoid duplicate scene names within a group listing (though unlikely)
                if scene_name not in scenes_by_group[group_name]:
                    scenes_by_group[group_name].append(scene_name)

        # Sort scenes within each group for consistent output
        for group_name in scenes_by_group:
            scenes_by_group[group_name].sort()

        return scenes_by_group
    except (PhueException, Exception) as e:
        # Return error as list to match other list-returning tools on error
        return [f"Error listing scenes by group: {e}"]


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
def activate_scene(group_name: str, scene_name: str) -> dict[str, Any]:
    """Activates a specific scene within a specified light group, verifying the scene belongs to the group."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}
    try:
        # 1. Find the target group ID
        groups_data = bridge.get_group()
        target_group_id = None
        for gid, ginfo in groups_data.items():
            if ginfo.get("name") == group_name:
                target_group_id = gid
                break
        if not target_group_id:
            return {"error": f"Group '{group_name}' not found", "success": False}

        # 2. Find the target scene and check its group association
        scenes_data = bridge.get_scene()
        scene_found = False
        scene_in_correct_group = False
        for sinfo in scenes_data.values():
            if sinfo.get("name") == scene_name:
                scene_found = True
                # Check if this scene is associated with the target group ID
                if sinfo.get("group") == target_group_id:
                    scene_in_correct_group = True
                    break  # Found the scene in the correct group

        if not scene_found:
            return {"error": f"Scene '{scene_name}' not found", "success": False}

        if not scene_in_correct_group:
            return {
                "error": f"Scene '{scene_name}' does not belong to group '{group_name}'",
                "success": False,
            }

        # 3. Activate the scene (now that we've verified it)
        result = bridge.run_scene(group_name=group_name, scene_name=scene_name)

        if result:
            return {
                "group": group_name,
                "activated_scene": scene_name,
                "success": True,
                "phue2_result": result,
            }
        else:
            # This case might indicate the scene/group exists but activation failed internally
            return {
                "group": group_name,
                "scene": scene_name,
                "error": "Scene activation failed (phue2 returned False)",
                "success": False,
            }

    except (KeyError, PhueException, Exception) as e:
        # Handle potential errors during bridge communication or data parsing
        return handle_phue_error(f"{group_name}/{scene_name}", "activate_scene", e)


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
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
        result = bridge.set_light(light_name, dict(attributes))
        return {
            "light": light_name,
            "set_attributes": attributes,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, ValueError, Exception) as e:
        # ValueError might occur for invalid attribute values
        return handle_phue_error(light_name, "set_light_attributes", e)


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
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
        result = bridge.set_group(group_name, dict(attributes))
        return {
            "group": group_name,
            "set_attributes": attributes,
            "success": True,
            "phue2_result": result,
        }
    except (KeyError, PhueException, ValueError, Exception) as e:
        return handle_phue_error(group_name, "set_group_attributes", e)


@lights_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def list_lights_by_group() -> dict[str, list[str]] | list[str]:
    """Lists Hue lights, grouped by the room/group they belong to.

    Returns:
        dict[str, list[str]]: A dictionary mapping group names to a list of light names within that group.
        list[str]: An error message list if the bridge connection fails or an error occurs.
    """
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        groups_data = bridge.get_group()  # dict {group_id: {details}}
        lights_data = bridge.get_light_objects("id")  # dict {light_id: {details}}

        lights_by_group: dict[str, list[str]] = {}
        for group_details in groups_data.values():
            group_name = group_details.get("name")
            light_ids = group_details.get("lights", [])
            if group_name and light_ids:
                light_names = []
                for light_id in light_ids:
                    # phue uses string IDs for lights in group, but int IDs in get_light_objects
                    light_id_int = int(light_id)
                    if light_id_int in lights_data:
                        light_name = lights_data[light_id_int].name
                        if light_name:
                            light_names.append(light_name)
                if light_names:
                    light_names.sort()
                    lights_by_group[group_name] = light_names

        return lights_by_group

    except (PhueException, Exception) as e:
        return [f"Error listing lights by group: {e}"]
