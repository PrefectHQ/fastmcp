"""Pause triage for an author who opens a burst of issues and pull requests.

One person opening a dozen PRs in a few minutes would otherwise start a full
triage run, and a failure alert, for each. Intake counts what the event's sender
opened in the repository within `window_hours`; past `max_items` it skips the
event. Slack gets one line, from the event that first crosses the limit.

The limit lives in the Prefect Variable `fastmcp_triage_config` under `burst`,
read on every run, so changing it needs no deploy.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPOSITORY = "PrefectHQ/fastmcp"


class BurstConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # The search lookup returns one page of 100, so the limit must stay below it.
    max_items: int = Field(default=5, ge=1, le=99)
    window_hours: int = Field(default=24, ge=1, le=168)


def burst_config(raw: object) -> BurstConfig:
    value = raw.get("burst") if isinstance(raw, dict) else None
    try:
        return BurstConfig.model_validate(value or {})
    except ValidationError:
        return BurstConfig()


def opened_by(read: Callable[[str], object], login: str, since: datetime) -> list[int]:
    """Issue and PR numbers `login` opened in the repository since `since`."""
    query = f"repo:{REPOSITORY} author:{login} created:>={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    result = read(f"search/issues?q={quote(query)}&per_page=100")
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ValueError("Invalid GitHub search result")
    return sorted(item["number"] for item in result["items"])


def burst(
    read: Callable[[str], object],
    login: str,
    trigger: int,
    config: BurstConfig,
    now: datetime,
) -> dict | None:
    """The burst `trigger` belongs to, or None when the author is under the limit.

    Counting only items numbered up to the trigger makes exactly one event, the
    first past the limit, the one that announces it, however late intake runs.
    """
    numbers = opened_by(read, login, now - timedelta(hours=config.window_hours))
    before = [number for number in numbers if number <= trigger]
    if len(before) <= config.max_items:
        return None
    return {
        "login": login,
        "count": len(numbers),
        "window_hours": config.window_hours,
        "announce": len(before) == config.max_items + 1,
        "crossing": before[config.max_items],
        "numbers": numbers,
    }


def burst_line(found: dict) -> str:
    login = found["login"]
    return (
        f"*Triage paused for @{login}:* {found['count']} issues and PRs opened in "
        f"{found['window_hours']}h, over the limit. Their new items skip triage until "
        f"the window passes. <https://github.com/{REPOSITORY}/issues?q=author%3A{login}|their items>"
    )
