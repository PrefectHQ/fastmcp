"""
Example server demonstrating fastmcp.json configuration.

This server previously would have used the deprecated dependencies parameter:
    mcp = FastMCP("Demo Server", dependencies=["pyautogui", "Pillow"])

Now dependencies are declared in fastmcp.json alongside this file.
"""

import io

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

# Create server - dependencies are now in fastmcp.json
mcp = FastMCP("Screenshot Demo")


@mcp.tool
def take_screenshot() -> Image:
    """
    Take a screenshot of the user's screen and return it as an image.

    Use this tool anytime the user wants you to look at something on their screen.
    """
    import pyautogui

    buffer = io.BytesIO()

    # Capture and compress the screenshot to stay under size limits
    screenshot = pyautogui.screenshot()
    screenshot.convert("RGB").save(buffer, format="JPEG", quality=60, optimize=True)

    return Image(data=buffer.getvalue(), format="jpeg")


@mcp.tool
def analyze_colors() -> dict:
    """
    Analyze the dominant colors in the current screen.

    Returns a dictionary with color statistics from the screen.
    """
    import pyautogui
    from PIL import Image as PILImage

    screenshot = pyautogui.screenshot()
    # Convert to smaller size for faster analysis
    small = screenshot.resize((100, 100), PILImage.Resampling.LANCZOS)

    # Get colors
    colors = small.getcolors(maxcolors=10000)
    if not colors:
        return {"error": "Too many colors to analyze"}

    # Sort by frequency
    sorted_colors = sorted(colors, key=lambda x: x[0], reverse=True)[:10]

    return {
        "top_colors": [
            {"count": count, "rgb": color} for count, color in sorted_colors
        ],
        "total_pixels": sum(c[0] for c in colors),
    }


if __name__ == "__main__":
    import asyncio

    asyncio.run(mcp.run_async())
