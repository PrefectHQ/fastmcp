# /// script
# dependencies = ["pyautogui", "Pillow", "fastmcp"]
# ///

"""
FastMCP Screenshot Example

Give Claude a tool to capture and view screenshots.
"""

import io

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

# Create server
# Dependencies are configured in screenshot.fastmcp.json
mcp = FastMCP("Screenshot Demo")


@mcp.tool
def take_screenshot() -> Image:
    """
    Take a screenshot of the user's screen and return it as an image. Use
    this tool anytime the user wants you to look at something they're doing.
    """
    import pyautogui

    buffer = io.BytesIO()

    # if the file exceeds ~1MB, it will be rejected by Claude
    screenshot = pyautogui.screenshot()
    screenshot.convert("RGB").save(buffer, format="JPEG", quality=60, optimize=True)
    return Image(data=buffer.getvalue(), format="jpeg")
