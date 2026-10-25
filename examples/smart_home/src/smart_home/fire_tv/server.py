"""A constrained Fire TV surface backed by Home Assistant's androidtv library."""

import re
from typing import Literal

from mcp_types import ToolAnnotations

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from smart_home.fire_tv.client import FireTVClient, fire_tv_lifespan, get_fire_tv
from smart_home.fire_tv.models import CommandReceipt, FireTVStatus

fire_tv_mcp = FastMCP(
    "Fire TV",
    lifespan=fire_tv_lifespan,
    instructions="Read status before control. Writes are accepted commands, not proof of resulting TV state; read status afterward. No raw ADB shell access is exposed.",
)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


async def _play_youtube_video(fire_tv: FireTVClient, video_id: str) -> None:
    if YOUTUBE_VIDEO_ID.fullmatch(video_id) is None:
        raise ToolError("YouTube video IDs must contain 11 URL-safe characters")

    await fire_tv.adb_shell(
        "am start -a android.intent.action.VIEW "
        f"-d https://www.youtube.com/watch?v={video_id} "
        "com.amazon.firetv.youtube"
    )


@fire_tv_mcp.tool(annotations=READ)
async def read_status(
    fire_tv: FireTVClient = Depends(get_fire_tv),
) -> FireTVStatus:
    """Read power/media state and the foreground app without changing the TV."""
    state, current_app, running_apps, hdmi_input = await fire_tv.update(
        get_running_apps=False, lazy=True
    )
    properties = fire_tv.device_properties
    return FireTVStatus(
        available=fire_tv.available,
        state=state,
        current_app=current_app,
        running_apps=running_apps,
        hdmi_input=hdmi_input,
        manufacturer=properties.get("manufacturer"),
        model=properties.get("model"),
    )


@fire_tv_mcp.tool(annotations=WRITE)
async def press_home(
    fire_tv: FireTVClient = Depends(get_fire_tv),
) -> CommandReceipt:
    """Press Home. This changes navigation only; read status afterward to verify."""
    await fire_tv.home()
    return CommandReceipt(command="home")


@fire_tv_mcp.tool(annotations=WRITE)
async def launch_app(
    package_id: str,
    fire_tv: FireTVClient = Depends(get_fire_tv),
) -> CommandReceipt:
    """Launch an installed app by exact package ID, then read status to verify."""
    if package_id not in (fire_tv.installed_apps or []):
        raise ToolError(f"Package {package_id!r} is not installed")
    await fire_tv.launch_app(package_id)
    return CommandReceipt(command=f"launch_app:{package_id}")


@fire_tv_mcp.tool(annotations=WRITE)
async def play_youtube_video(
    video_id: str,
    fire_tv: FireTVClient = Depends(get_fire_tv),
) -> CommandReceipt:
    """Open one YouTube video by its 11-character ID, then verify via status."""
    await _play_youtube_video(fire_tv, video_id)
    return CommandReceipt(command=f"play_youtube_video:{video_id}")


@fire_tv_mcp.tool(annotations=WRITE)
async def play_media(
    source: Literal["youtube"],
    source_id: str,
    url: str,
    title: str,
    fire_tv: FireTVClient = Depends(get_fire_tv),
) -> CommandReceipt:
    """Play normalized media from another MCP server; currently supports YouTube."""
    del url, title
    if source != "youtube":
        raise ToolError(f"Fire TV playback does not support source {source!r}")
    await _play_youtube_video(fire_tv, source_id)
    return CommandReceipt(command=f"play_media:{source}:{source_id}")
