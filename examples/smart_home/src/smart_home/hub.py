from fastmcp import FastMCP
from smart_home.lights.server import lights_mcp

hub_mcp = FastMCP("Smart home")
hub_mcp.mount(lights_mcp, namespace="hue")
