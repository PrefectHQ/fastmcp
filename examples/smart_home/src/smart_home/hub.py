from fastmcp import FastMCP
from smart_home.lights.server import bridge, lights_mcp

hub_mcp = FastMCP(
    "Smart Home Hub (phue2)",
    dependencies=[
        "smart_home@git+https://github.com/jlowin/fastmcp.git@n8example#subdirectory=examples/smart_home",
    ],
)

# Mount the lights service under the 'hue' prefix
hub_mcp.mount("hue", lights_mcp)


# Add a status check for the hub
@hub_mcp.tool()
def hub_status() -> str:
    """Checks the status of the main hub and connections."""
    if bridge:
        # Access the bridge instance directly
        return "Hub OK. Hue Bridge Connected (via phue2)."
    else:
        return "Hub Warning: Hue Bridge connection failed or not attempted."


# Add mounting points for other services later
# hub_mcp.mount("thermo", thermostat_mcp)
