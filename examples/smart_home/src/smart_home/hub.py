from mcp.types import ToolAnnotations
from phue2 import Bridge

from fastmcp import FastMCP
from smart_home.lights.server import lights_mcp
from smart_home.settings import settings

hub_mcp = FastMCP("Smart Home Hub (phue2)")

# Mount the lights service under the 'hue' namespace
hub_mcp.mount(lights_mcp, namespace="hue")


# Add a status check for the hub
@hub_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def hub_status() -> str:
    """Checks the status of the main hub and connections."""
    try:
        bridge = Bridge(
            ip=settings.hue_bridge_ip,
            username=settings.hue_bridge_username,
            save_config=False,
        )
        bridge.connect()
        return "Hub OK. Hue Bridge Connected (via phue2)."
    except Exception as e:
        return f"Hub Warning: Hue Bridge connection failed or not attempted: {e}"


# Add mounting points for other services later
# hub_mcp.mount("thermo", thermostat_mcp)
