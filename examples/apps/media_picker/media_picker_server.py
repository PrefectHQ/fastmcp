"""Media picker — an interactive, source-agnostic MCP App.

The model discovers normalized candidates, then opens a card picker. Buttons in
the app call backend tools directly or send a follow-up message to the model.

Usage:
    uv run python media_picker_server.py
"""

from __future__ import annotations

import os
from typing import Literal, TypedDict

from mcp.types import ToolAnnotations
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
    Column,
    Heading,
    If,
    Muted,
    Row,
    Text,
)
from prefab_ui.rx import ERROR, RESULT, Rx

from fastmcp import Client, FastMCP, FastMCPApp
from fastmcp.exceptions import ToolError

Source = Literal["youtube", "internet_archive", "live_cam"]
ALL_SOURCES: frozenset[Source] = frozenset({"youtube", "internet_archive", "live_cam"})


class MediaCandidate(TypedDict):
    id: str
    title: str
    source: Source
    source_id: str
    duration: str
    summary: str
    why: str
    tags: list[str]
    url: str


CATALOG: list[MediaCandidate] = [
    {
        "id": "cold-fusion-documentary",
        "title": "BobbyBroccoli — The Cold Fusion Scandal",
        "source": "youtube",
        "source_id": "jkAw87ZIwQA",
        "duration": "3h 15m",
        "summary": "A feature-length documentary about the cold fusion scandal.",
        "why": "A deeply researched story with enough room to settle in.",
        "tags": ["science", "history", "documentary", "long-form"],
        "url": "https://www.youtube.com/watch?v=jkAw87ZIwQA",
    },
    {
        "id": "forest-bird-feeder-live",
        "title": "HANS Nature — Live Bird Feeder and Forest Wildlife",
        "source": "youtube",
        "source_id": "D1VM6V6wmU0",
        "duration": "Live",
        "summary": "A fixed woodland camera focused on a busy bird feeder.",
        "why": "Natural forest sound, no host, and no soundtrack.",
        "tags": ["birds", "forest", "wildlife", "ambient", "no music"],
        "url": "https://www.youtube.com/watch?v=D1VM6V6wmU0",
    },
    {
        "id": "apollo-control-room",
        "title": "Apollo Mission Control Room Recordings",
        "source": "internet_archive",
        "source_id": "apollo-control-room",
        "duration": "3h 05m",
        "summary": "Restored mission audio paired with archival visuals.",
        "why": "Absorbing primary-source material with a slow pace.",
        "tags": ["space", "history", "archive", "documentary"],
        "url": "https://archive.org/",
    },
    {
        "id": "monterey-bay-live",
        "title": "Monterey Bay Open Ocean Live Cam",
        "source": "live_cam",
        "source_id": "open-sea-cam",
        "duration": "Live",
        "summary": "Open-ocean wildlife from a fixed underwater camera.",
        "why": "Genuinely live, visually calm, and naturally unpredictable.",
        "tags": ["ocean", "wildlife", "live", "ambient", "no music"],
        "url": "https://www.montereybayaquarium.org/animals/live-cams/open-sea-cam",
    },
]

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

app = FastMCPApp("Media Picker")
mcp = FastMCP("Media Picker", providers=[app])
_saved_ids: set[str] = set()


def _candidate(media_id: str) -> MediaCandidate:
    try:
        return next(item for item in CATALOG if item["id"] == media_id)
    except StopIteration as exc:
        raise ValueError(f"Unknown media id: {media_id}") from exc


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


def _is_playable(item: MediaCandidate) -> bool:
    sources = _actuator_sources()
    return sources is None or item["source"] in sources


async def _play_via_mcp(
    item: MediaCandidate, target: FastMCP | str, tool_name: str
) -> object:
    async with Client(target) as client:
        result = await client.call_tool(
            tool_name,
            {
                "source": item["source"],
                "source_id": item["source_id"],
                "url": item["url"],
                "title": item["title"],
            },
            raise_on_error=False,
        )
    if result.is_error:
        raise ToolError("The playback device could not start this item.")
    return result.data


@mcp.tool(annotations=READ_ONLY)
def discover_media(
    query: str = "",
    sources: list[Source] | None = None,
) -> list[MediaCandidate]:
    """Use this when the user wants media choices. Returns normalized candidates.

    Search first, then call `show_media_picker` with the returned candidate IDs.
    An empty query returns the full demo catalog.
    """
    allowed_sources = set(sources) if sources else None
    terms = query.lower().split()

    def matches(item: MediaCandidate) -> bool:
        if allowed_sources is not None and item["source"] not in allowed_sources:
            return False
        haystack = " ".join(
            [item["title"], item["summary"], item["why"], *item["tags"]]
        ).lower()
        return not terms or all(term in haystack for term in terms)

    return [item for item in CATALOG if matches(item)]


@app.tool()
async def play_media(media_id: str) -> dict[str, object]:
    """Play one candidate through the configured MCP actuator, or return a demo receipt."""
    item = _candidate(media_id)
    actuator_url = os.getenv("MEDIA_PICKER_ACTUATOR_URL")
    if actuator_url:
        if not _is_playable(item):
            source = item["source"].replace("_", " ").title()
            raise ToolError(f"{source} playback is not available on this device.")
        tool_name = os.getenv("MEDIA_PICKER_ACTUATOR_TOOL", "play_media")
        receipt = await _play_via_mcp(item, actuator_url, tool_name)
        return {
            "action": "play",
            "media_id": media_id,
            "status": "accepted",
            "title": item["title"],
            "url": item["url"],
            "actuator": tool_name,
            "receipt": receipt,
            "message": f"Sent “{item['title']}” to the playback device.",
        }

    return {
        "action": "play",
        "media_id": media_id,
        "status": "queued",
        "title": item["title"],
        "url": item["url"],
        "message": f"Queued “{item['title']}” for playback.",
    }


@app.tool()
def save_media(media_id: str) -> dict[str, str]:
    """Save one candidate idempotently and return a receipt."""
    item = _candidate(media_id)
    status = "already_saved" if media_id in _saved_ids else "saved"
    _saved_ids.add(media_id)
    return {
        "action": "save",
        "media_id": media_id,
        "status": status,
        "title": item["title"],
        "message": (
            f"“{item['title']}” was already saved."
            if status == "already_saved"
            else f"Saved “{item['title']}”."
        ),
    }


@app.ui(annotations=READ_ONLY)
def show_media_picker(candidate_ids: list[str] | None = None) -> PrefabApp:
    """Use this after `discover_media` to show its candidates as an interactive picker."""
    candidates = (
        [_candidate(media_id) for media_id in candidate_ids]
        if candidate_ids is not None
        else list(CATALOG)
    )
    requested_count = len(candidates)
    candidates = [item for item in candidates if _is_playable(item)]
    omitted_count = requested_count - len(candidates)

    with Column(gap=4, css_class="p-4") as view:
        Heading("Pick something worth watching")
        Muted("Choose a result to play directly on the configured device.")

        if omitted_count:
            Muted(
                f"{omitted_count} result{'s' if omitted_count != 1 else ''} hidden "
                "because this device does not support that source."
            )

        if not candidates:
            with Card():
                with CardContent(css_class="p-5"):
                    Text("No playable candidates matched. Try another discovery query.")
        else:
            with Column(gap=3):
                for item in candidates:
                    with Card():
                        with CardHeader():
                            with Row(gap=2, align="center"):
                                Badge(item["source"].replace("_", " ").title())
                                Badge(item["duration"], variant="outline")
                            CardTitle(item["title"])
                            CardDescription(item["summary"])

                        with CardContent():
                            Text(item["why"])
                            Muted(" · ".join(item["tags"]))

                        with CardFooter():
                            with Row(gap=2):
                                Button(
                                    "Play",
                                    icon="play",
                                    on_click=CallTool(
                                        play_media,
                                        arguments={"media_id": item["id"]},
                                        on_success=[
                                            SetState("last_action", RESULT.message),
                                            ShowToast("Sent to TV", variant="success"),
                                        ],
                                        on_error=ShowToast(ERROR, variant="error"),
                                    ),
                                )
                                Button(
                                    "Save",
                                    variant="outline",
                                    icon="bookmark",
                                    on_click=CallTool(
                                        save_media,
                                        arguments={"media_id": item["id"]},
                                        on_success=[
                                            SetState("last_action", RESULT.message),
                                            ShowToast("Saved", variant="success"),
                                        ],
                                        on_error=ShowToast(ERROR, variant="error"),
                                    ),
                                )
                                Button(
                                    "More like this",
                                    variant="ghost",
                                    on_click=SendMessage(
                                        f"Find more media like “{item['title']}”, "
                                        "then reopen the media picker with the new candidates."
                                    ),
                                )

        with If(Rx("last_action")):
            Text(Rx("last_action"), css_class="text-sm font-medium")

    return PrefabApp(
        view=view,
        state={
            "last_action": "",
            "candidate_ids": [item["id"] for item in candidates],
            "omitted_count": omitted_count,
        },
    )


if __name__ == "__main__":
    mcp.run(transport="http")
