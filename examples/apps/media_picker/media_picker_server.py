"""Media picker — an interactive, source-agnostic MCP App.

The model discovers normalized candidates, then opens a card picker. Buttons in
the app call backend tools directly or send a follow-up message to the model.

Usage:
    uv run python media_picker_server.py
"""

from __future__ import annotations

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
    Carousel,
    Column,
    Heading,
    If,
    Muted,
    Row,
    Text,
)
from prefab_ui.rx import ERROR, RESULT, Rx

from fastmcp import FastMCP, FastMCPApp

Source = Literal["youtube", "internet_archive", "live_cam"]


class MediaCandidate(TypedDict):
    id: str
    title: str
    source: Source
    duration: str
    summary: str
    why: str
    tags: list[str]
    url: str


CATALOG: list[MediaCandidate] = [
    {
        "id": "tokyo-rain-walk",
        "title": "Tokyo After Midnight — Rain Walk",
        "source": "youtube",
        "duration": "2h 18m",
        "summary": "A quiet, uncut walk through rainy side streets.",
        "why": "Natural city sound, no host, no soundtrack.",
        "tags": ["rain", "city", "walking", "ambient", "no music"],
        "url": "https://www.youtube.com/",
    },
    {
        "id": "norway-night-train",
        "title": "Night Train Across Norway",
        "source": "youtube",
        "duration": "7h 14m",
        "summary": "A driver's-eye rail journey through the night.",
        "why": "Long-form, steady visuals, only train and track sound.",
        "tags": ["train", "travel", "night", "ambient", "no music"],
        "url": "https://www.youtube.com/",
    },
    {
        "id": "apollo-control-room",
        "title": "Apollo Mission Control Room Recordings",
        "source": "internet_archive",
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
def play_media(media_id: str) -> dict[str, str]:
    """Queue one candidate for playback and return an actuator-friendly receipt."""
    item = _candidate(media_id)
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

    with Column(gap=5, css_class="p-5") as view:
        Heading("Pick something worth watching")
        Muted("Normalized results can come from any source; actions stay the same.")

        if not candidates:
            with Card():
                with CardContent(css_class="p-5"):
                    Text("No candidates matched. Try a broader discovery query.")
        else:
            with Carousel(visible=1.15, gap=16, loop=False, show_dots=True):
                for item in candidates:
                    with Card(css_class="h-full min-h-80"):
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
                                            ShowToast(
                                                "Queued for playback", variant="success"
                                            ),
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
        },
    )


if __name__ == "__main__":
    mcp.run(transport="http")
