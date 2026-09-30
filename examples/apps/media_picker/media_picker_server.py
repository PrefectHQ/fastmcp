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

import httpx2
from mcp.types import ToolAnnotations
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Button,
    Column,
    Div,
    Muted,
    Row,
    Text,
)
from prefab_ui.rx import ERROR, RESULT
from pydantic import BaseModel, Field

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError

Source = Literal["youtube"]
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


app = FastMCPApp("Media Picker")
mcp = FastMCP("Media Picker", providers=[app])
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


def _checked(source: Source, source_id: str) -> str:
    if source != "youtube" or YOUTUBE_ID.fullmatch(source_id) is None:
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
    playable = candidates
    unplayable = unsupported
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


@app.ui(annotations=READ_ONLY)
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


if __name__ == "__main__":
    mcp.run(transport="http")
