from typing import Any

from phue2 import Bridge
from phue2.exceptions import (
    PhueException,
)

from fastmcp import FastMCP
from smart_home.settings import settings


def _get_bridge() -> Bridge | None:
    try:
        return Bridge(
            ip=str(settings.hue_bridge_ip),
            username=settings.hue_bridge_username,
            save_config=False,
        )
    except Exception:
        return None


lights_mcp = FastMCP(
    "Hue Lights Service (phue2)",
    dependencies=[
        "smart_home@git+https://github.com/jlowin/fastmcp.git@n8example#subdirectory=examples/smart_home",
    ],
)

# --- Resources ---


@lights_mcp.tool()
def read_all_lights() -> list[str]:
    """Lists the names of all available Hue lights using phue2."""
    if not (bridge := _get_bridge()):
        return ["Error: Bridge not connected"]
    try:
        light_dict = bridge.get_light_objects("list")
        return [light.name for light in light_dict]
    except PhueException as e:
        return [f"Error listing lights: {e}"]
    except Exception as e:
        return [f"Unexpected error listing lights: {e}"]


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
    except KeyError:
        return {
            "light": light_name,
            "error": f"Light '{light_name}' not found",
            "success": False,
        }
    except PhueException as e:
        return {
            "light": light_name,
            "error": f"phue2 error toggling light: {e}",
            "success": False,
        }
    except Exception as e:
        return {
            "light": light_name,
            "error": f"Unexpected error toggling light: {e}",
            "success": False,
        }


@lights_mcp.tool()
def set_brightness(light_name: str, brightness: int) -> dict[str, Any]:
    """Sets the brightness of a specific light (0-254) using phue2."""
    if not (bridge := _get_bridge()):
        return {"error": "Bridge not connected", "success": False}
    if not 0 <= brightness <= 254:
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
    except KeyError:
        return {
            "light": light_name,
            "error": f"Light '{light_name}' not found",
            "success": False,
        }
    except PhueException as e:
        return {
            "light": light_name,
            "error": f"phue2 error setting brightness: {e}",
            "success": False,
        }
    except Exception as e:
        return {
            "light": light_name,
            "error": f"Unexpected error setting brightness: {e}",
            "success": False,
        }
