import sys
from typing import Any

from phue2 import Bridge
from phue2.exceptions import (
    PhueException,
    PhueRegistrationException,
    PhueRequestTimeout,
)

from fastmcp import FastMCP
from smart_home.settings import settings

bridge: Bridge | None = None
BRIDGE_IP = str(settings.hue_bridge_ip)

try:
    bridge = Bridge(BRIDGE_IP)
    print(f"Attempting connection to Hue Bridge at {BRIDGE_IP} via phue2...")
    bridge.connect()  # Explicitly connect (needed for registration check)
    print(f"Successfully connected to Hue Bridge at {BRIDGE_IP} via phue2.")

except PhueRegistrationException:
    print(
        f"FATAL: phue2 not registered with Bridge {BRIDGE_IP}. Run registration first.",
        file=sys.stderr,
    )
    bridge = None
except PhueRequestTimeout:
    print(
        f"FATAL: Timeout connecting to Hue Bridge at {BRIDGE_IP}. Check IP and network.",
        file=sys.stderr,
    )
    bridge = None
except PhueException as e:
    print(f"FATAL: phue2 error connecting to Bridge {BRIDGE_IP}: {e}", file=sys.stderr)
    bridge = None
except Exception as e:
    print(
        f"FATAL: Unexpected error connecting to Bridge {BRIDGE_IP}: {e}",
        file=sys.stderr,
    )
    bridge = None

lights_mcp = FastMCP(
    "Hue Lights Service (phue2)",
    dependencies=["phue2", "fastmcp@git+https://github.com/jlowin/fastmcp.git"],
)

# --- Resources ---


@lights_mcp.resource("hue://lights")
def list_lights() -> list[str]:
    """Lists the names of all available Hue lights using phue2."""
    if not bridge:
        return ["Error: Bridge not connected"]
    try:
        light_dict = bridge.get_light()
        return list(light_dict.keys())
    except PhueException as e:
        return [f"Error listing lights: {e}"]
    except Exception as e:
        return [f"Unexpected error listing lights: {e}"]


@lights_mcp.resource("hue://light/{light_name}/status")
def get_light_status(light_name: str) -> dict[str, Any]:
    """Gets the current status of a specific light using phue2."""
    if not bridge:
        return {"error": "Bridge not connected"}
    try:
        light_state = bridge.get_light(light_name, "state")
        if light_state and isinstance(light_state, dict):
            return {
                "name": light_name,
                "on": light_state.get("on"),
                "brightness": light_state.get("bri"),
                "reachable": light_state.get("reachable"),
                "raw_state": light_state,
            }
        else:
            return {"error": f"Light '{light_name}' not found or state unavailable."}
    except KeyError:
        return {"error": f"Light '{light_name}' not found (KeyError)."}
    except PhueException as e:
        return {"error": f"phue2 error getting status for '{light_name}': {e}"}
    except Exception as e:
        return {"error": f"Unexpected error getting status for '{light_name}': {e}"}


# --- Tools ---


@lights_mcp.tool()
def toggle_light(light_name: str, state: bool) -> dict[str, Any]:
    """Turns a specific light on (true) or off (false) using phue2."""
    if not bridge:
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
    if not bridge:
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
