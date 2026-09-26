"""Media picker — an interactive MCP App that plays verified links on a TV.

The model finds media with its own search and opens the picker with links. The
server verifies each link with its source, renders the playable ones as cards,
and buttons in the app call backend tools directly or send a follow-up message
to the model.

Usage:
    uv run python media_picker_server.py
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qs, urlparse

import home_lights
import httpx2
from home_lights import RoomView, call_lights, checked_id, read_home
from mcp.types import ToolAnnotations
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Button,
    Column,
    Div,
    Image,
    Muted,
    Row,
    Switch,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.rx import ERROR, EVENT, RESULT
from pydantic import BaseModel, Field

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.apps.config import ResourceCSP
from fastmcp.exceptions import ToolError
from fastmcp.experimental.auth.atproto import ATProtoProvider

Source = Literal["youtube"]
ALL_SOURCES: frozenset[Source] = frozenset({"youtube"})
MAX_LINKS = 12

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_OEMBED_URL = "https://www.youtube.com/oembed"


class MediaLink(BaseModel):
    url: str = Field(description="A YouTube video or livestream URL.")
    why: str = Field(
        default="", description="One sentence on why this fits the request."
    )
    label: str = Field(
        default="", description='Optional length label, such as "Live" or "3h 15m".'
    )


class MediaCandidate(TypedDict):
    source: Source
    source_id: str
    url: str
    title: str
    channel: str
    why: str
    label: str


READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def _auth_from_env() -> ATProtoProvider | None:
    """Require AT Protocol sign-in when MEDIA_PICKER_BASE_URL is set."""
    base_url = os.getenv("MEDIA_PICKER_BASE_URL")
    if not base_url:
        return None
    allowed_dids = [
        did.strip()
        for did in os.getenv("MEDIA_PICKER_ALLOWED_DIDS", "").split(",")
        if did.strip()
    ]
    if not allowed_dids:
        raise ValueError(
            "MEDIA_PICKER_ALLOWED_DIDS is required with MEDIA_PICKER_BASE_URL"
        )
    return ATProtoProvider(
        base_url=base_url,
        jwt_signing_key=os.environ["MEDIA_PICKER_JWT_SIGNING_KEY"],
        allowed_dids=allowed_dids,
        require_authorization_consent="remember",
    )


app = FastMCPApp("Media Picker")
mcp = FastMCP("Media Picker", providers=[app], auth=_auth_from_env())
_saved: dict[str, MediaCandidate] = {}


def youtube_video_id(url: str) -> str | None:
    """Extract a video ID from the common YouTube URL shapes."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host == "youtu.be" and parts:
        candidate = parts[0]
    elif host == "youtube.com":
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in ("live", "shorts", "embed", "v"):
            candidate = parts[1]
    return candidate if YOUTUBE_ID.fullmatch(candidate) else None


def _oembed_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=5.0)


async def _verify_youtube(
    client: httpx2.AsyncClient, link: MediaLink, video_id: str
) -> MediaCandidate | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = await client.get(
            YOUTUBE_OEMBED_URL, params={"url": url, "format": "json"}
        )
    except httpx2.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    title = data.get("title")
    if not isinstance(title, str) or not title:
        return None
    channel = data.get("author_name")
    return {
        "source": "youtube",
        "source_id": video_id,
        "url": url,
        "title": title,
        "channel": channel if isinstance(channel, str) else "",
        "why": link.why,
        "label": link.label,
    }


async def verify_links(
    links: list[MediaLink],
) -> tuple[list[MediaCandidate], int, int]:
    """Return verified candidates plus counts of unsupported and unverified links."""
    parsed = [(link, youtube_video_id(link.url)) for link in links[:MAX_LINKS]]
    unsupported = sum(1 for _, video_id in parsed if video_id is None)
    async with _oembed_client() as client:
        results = await asyncio.gather(
            *(
                _verify_youtube(client, link, video_id)
                for link, video_id in parsed
                if video_id is not None
            )
        )
    candidates: list[MediaCandidate] = []
    seen: set[str] = set()
    unverified = 0
    for result in results:
        if result is None:
            unverified += 1
        elif result["source_id"] not in seen:
            seen.add(result["source_id"])
            candidates.append(result)
    return candidates, unsupported, unverified


def _actuator_sources() -> frozenset[Source] | None:
    """Return configured actuator capabilities, or None in demo mode."""
    if not os.getenv("MEDIA_PICKER_ACTUATOR_URL"):
        return None

    configured = {
        source.strip()
        for source in os.getenv("MEDIA_PICKER_ACTUATOR_SOURCES", "").split(",")
        if source.strip()
    }
    unknown = configured - ALL_SOURCES
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ToolError(f"Unknown playback source in configuration: {names}")
    return frozenset(source for source in ALL_SOURCES if source in configured)


def _is_playable(source: Source) -> bool:
    sources = _actuator_sources()
    return sources is None or source in sources


def _checked(source: Source, source_id: str) -> str:
    if source not in ALL_SOURCES or YOUTUBE_ID.fullmatch(source_id) is None:
        raise ToolError("That media link is not valid.")
    return f"https://www.youtube.com/watch?v={source_id}"


async def _play_via_mcp(
    source: Source,
    source_id: str,
    url: str,
    title: str,
    target: FastMCP | str,
    tool_name: str,
) -> object:
    async with Client(target) as client:
        result = await client.call_tool(
            tool_name,
            {"source": source, "source_id": source_id, "url": url, "title": title},
            raise_on_error=False,
        )
    if result.is_error:
        raise ToolError("The playback device could not start this item.")
    return result.data


@app.tool()
async def play_media(source: Source, source_id: str, title: str) -> dict[str, object]:
    """Play one verified item through the configured MCP actuator, or return a demo receipt."""
    url = _checked(source, source_id)
    actuator_url = os.getenv("MEDIA_PICKER_ACTUATOR_URL")
    if actuator_url:
        if not _is_playable(source):
            raise ToolError("That source is not available on this device.")
        tool_name = os.getenv("MEDIA_PICKER_ACTUATOR_TOOL", "play_media")
        receipt = await _play_via_mcp(
            source, source_id, url, title, actuator_url, tool_name
        )
        return {
            "action": "play",
            "source_id": source_id,
            "status": "accepted",
            "title": title,
            "url": url,
            "actuator": tool_name,
            "receipt": receipt,
            "message": f"Sent “{title}” to the playback device.",
        }

    return {
        "action": "play",
        "source_id": source_id,
        "status": "queued",
        "title": title,
        "url": url,
        "message": f"Queued “{title}” for playback.",
    }


@app.tool()
def save_media(source: Source, source_id: str, title: str) -> dict[str, str]:
    """Save one verified item idempotently and return a receipt."""
    url = _checked(source, source_id)
    key = f"{source}:{source_id}"
    status = "already_saved" if key in _saved else "saved"
    _saved[key] = {
        "source": source,
        "source_id": source_id,
        "url": url,
        "title": title,
        "channel": "",
        "why": "",
        "label": "",
    }
    return {
        "action": "save",
        "source_id": source_id,
        "status": status,
        "title": title,
        "message": (
            f"“{title}” was already saved."
            if status == "already_saved"
            else f"Saved “{title}”."
        ),
    }


async def _playable_links(
    links: list[MediaLink],
) -> tuple[list[MediaCandidate], list[str], int, int]:
    candidates, unsupported, unverified = await verify_links(links)
    playable = [item for item in candidates if _is_playable(item["source"])]
    unplayable = unsupported + len(candidates) - len(playable)
    notices = []
    if unverified:
        notices.append(
            f"{unverified} link{'s' if unverified != 1 else ''} couldn't be verified."
        )
    if unplayable:
        notices.append(
            f"{unplayable} link{'s' if unplayable != 1 else ''} can't play on this device."
        )
    return playable, notices, unverified, unplayable


THUMBNAIL_CSP = ResourceCSP(resource_domains=["https://i.ytimg.com"])
LEVELS = (10, 40, 70, 100)


def _toast_result() -> list[Any]:
    return [
        SetState("last_action", RESULT.message),
        ShowToast(RESULT.message, variant="success"),
    ]


def _media_list(playable: list[MediaCandidate], notices: list[str]) -> None:
    if notices:
        Muted(" ".join(notices), css_class="notice")

    if not playable:
        with Div(css_class="empty"):
            Text("nothing queued")
            Muted("ask claude for something to watch and it lands here.")
        return

    with Column(css_class="picks"):
        for item in playable:
            identity = {
                "source": item["source"],
                "source_id": item["source_id"],
                "title": item["title"],
            }
            with Div(css_class="pick"):
                Image(
                    src=f"https://i.ytimg.com/vi/{item['source_id']}/mqdefault.jpg",
                    alt="",
                    css_class="thumb",
                )
                with Column(css_class="pick-copy"):
                    Text(item["title"], css_class="pick-title")
                    with Row(css_class="pick-meta"):
                        Text(item["channel"] or "youtube", css_class="channel")
                        if item["label"]:
                            Text(
                                item["label"].lower(),
                                css_class="tag live"
                                if item["label"].lower() == "live"
                                else "tag",
                            )
                    if item["why"]:
                        Muted(item["why"], css_class="why")
                    with Row(css_class="pick-actions"):
                        Button(
                            "save",
                            variant="ghost",
                            size="xs",
                            css_class="quiet",
                            on_click=CallTool(
                                save_media,
                                arguments=identity,
                                on_success=_toast_result(),
                                on_error=ShowToast(ERROR, variant="error"),
                            ),
                        )
                        Button(
                            "more like this",
                            variant="ghost",
                            size="xs",
                            css_class="quiet",
                            on_click=SendMessage(
                                f"Find more media like “{item['title']}”, "
                                "then reopen the home view with the new links."
                            ),
                        )
                Button(
                    "play on tv" if os.getenv("MEDIA_PICKER_ACTUATOR_URL") else "play",
                    icon="play",
                    size="icon",
                    css_class="play",
                    on_click=CallTool(
                        play_media,
                        arguments=identity,
                        on_success=_toast_result(),
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                )


def _css() -> list[str]:
    return [Path(__file__).with_name("media_picker.css").read_text()]


def _header(title: str, status: str) -> None:
    with Row(css_class="head"):
        Text(title, css_class="title")
        Muted(status, css_class="status")


@app.ui(annotations=READ_ONLY, csp=THUMBNAIL_CSP)
async def show_media_picker(links: list[MediaLink]) -> PrefabApp:
    """Show media choices the user can play on their TV.

    Find candidates first with your own web search, then pass their YouTube
    URLs here. Each link is verified with YouTube before it is shown, so pass
    real URLs you found, never guessed ones. Prefer 3-6 varied options.
    """
    playable, notices, unverified, unplayable = await _playable_links(links)

    with Column(css_class="home") as view:
        _header("watch", f"{len(playable)} to pick from" if playable else "")
        _media_list(playable, notices)

    return PrefabApp(
        view=view,
        css=_css(),
        state={
            "last_action": "",
            "source_ids": [item["source_id"] for item in playable],
            "unverified_count": unverified,
            "unplayable_count": unplayable,
        },
    )


def _light_receipt(message: str) -> dict[str, str]:
    return {"status": "accepted", "message": message}


@app.tool()
async def set_room_power(room_id: str, room_name: str, on: bool) -> dict[str, str]:
    """Turn every light in one room on or off."""
    await call_lights("set_room", {"target": checked_id(room_id), "state": {"on": on}})
    return _light_receipt(f"{room_name} {'on' if on else 'off'}.")


@app.tool()
async def set_room_brightness(
    room_id: str, room_name: str, brightness: int
) -> dict[str, str]:
    """Set one room's brightness percent, turning it on."""
    if not 1 <= brightness <= 100:
        raise ToolError("Brightness must be between 1 and 100.")
    await call_lights(
        "set_room",
        {
            "target": checked_id(room_id),
            "state": {"on": True, "brightness": brightness, "transition_seconds": 0.4},
        },
    )
    return _light_receipt(f"{room_name} at {brightness}%.")


@app.tool()
async def activate_room_scene(
    room_id: str, scene_id: str, scene_name: str
) -> dict[str, str]:
    """Recall one saved Hue scene in a room."""
    await call_lights(
        "activate_scene", {"room": checked_id(room_id), "scene": checked_id(scene_id)}
    )
    return _light_receipt(f"{scene_name} on.")


def _nearest_level(brightness: int) -> int:
    return min(LEVELS, key=lambda level: abs(level - brightness))


def _room_row(room: RoomView) -> None:
    lit = room["lights_on"] > 0
    accent = room["color"] or "#f2c27b"
    with Div(
        css_class="room lit" if lit else "room",
        style={"--accent": accent} if lit else None,
    ):
        with Row(css_class="room-head"):
            Div(css_class="lamp")
            Text(room["name"], css_class="room-name")
            Muted(
                f"{room['brightness']}%"
                if lit and room["lights_on"] == room["lights_total"]
                else f"{room['lights_on']}/{room['lights_total']} · {room['brightness']}%"
                if lit
                else "off",
                css_class="level-readout",
            )
            Switch(
                value=lit,
                css_class="room-switch",
                on_change=CallTool(
                    set_room_power,
                    arguments={
                        "room_id": room["id"],
                        "room_name": room["name"],
                        "on": EVENT,
                    },
                    on_success=ShowToast(RESULT.message, variant="success"),
                    on_error=ShowToast(ERROR, variant="error"),
                ),
            )
        with Row(css_class="room-controls"):
            current = _nearest_level(room["brightness"]) if lit else None
            with Row(css_class="levels"):
                for level in LEVELS:
                    Button(
                        str(level),
                        variant="ghost",
                        size="xs",
                        css_class="level current" if level == current else "level",
                        on_click=CallTool(
                            set_room_brightness,
                            arguments={
                                "room_id": room["id"],
                                "room_name": room["name"],
                                "brightness": level,
                            },
                            on_success=ShowToast(RESULT.message, variant="success"),
                            on_error=ShowToast(ERROR, variant="error"),
                        ),
                    )
            if room["scenes"]:
                with Row(css_class="scenes"):
                    for scene in room["scenes"]:
                        with Div(
                            css_class="scene active" if scene["active"] else "scene",
                            style={"--swatch": scene["color"]}
                            if scene["color"]
                            else None,
                        ):
                            Button(
                                scene["name"],
                                variant="ghost",
                                size="xs",
                                on_click=CallTool(
                                    activate_room_scene,
                                    arguments={
                                        "room_id": room["id"],
                                        "scene_id": scene["id"],
                                        "scene_name": scene["name"],
                                    },
                                    on_success=ShowToast(
                                        RESULT.message, variant="success"
                                    ),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )


@app.ui(annotations=READ_ONLY, csp=THUMBNAIL_CSP)
async def show_home(links: list[MediaLink] | None = None) -> PrefabApp:
    """Show the home controls: every room's lights, plus media to play on the TV.

    Call with no links to control lights. To suggest something to watch, find
    candidates with your own web search and pass their YouTube URLs as links;
    each is verified before it is shown.
    """
    rooms = await read_home() if home_lights.lights_target() else []
    playable, notices, _, _ = await _playable_links(links or [])
    lit = sum(room["lights_on"] > 0 for room in rooms)

    with Column(css_class="home") as view:
        _header(
            "home",
            f"{lit} of {len(rooms)} rooms lit" if rooms else "lights and tv",
        )
        with Tabs(value="watch" if playable else "lights", variant="line"):
            with Tab(title="lights", value="lights"):
                if rooms:
                    with Column(css_class="rooms"):
                        for room in rooms:
                            _room_row(room)
                else:
                    with Div(css_class="empty"):
                        Text("lights aren't connected")
                        Muted("set MEDIA_PICKER_LIGHTS_URL on the server.")
            with Tab(title="watch", value="watch"):
                _media_list(playable, notices)

    return PrefabApp(
        view=view,
        css=_css(),
        state={
            "last_action": "",
            "room_ids": [room["id"] for room in rooms],
            "source_ids": [item["source_id"] for item in playable],
        },
    )


if __name__ == "__main__":
    mcp.run(transport="http")
