"""Fallback poll cadence for client-side task waiting.

Two modes: a server-advertised pollInterval is honored exactly, while an
unadvertised one falls back to an exponential ramp up to the client setting.
"""

import pytest
from mcp_types import GetTaskResult
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.client.tasks import MIN_POLL_INTERVAL, ToolTask
from fastmcp.settings import Settings
from fastmcp.utilities.tests import temporary_settings


@pytest.mark.parametrize("value", [0, -0.5, -1])
def test_non_positive_poll_interval_setting_is_rejected(value: float):
    with pytest.raises(ValidationError):
        Settings(client_task_poll_interval=value)


def test_positive_poll_interval_setting_is_accepted():
    settings = Settings(client_task_poll_interval=0.25)
    assert settings.client_task_poll_interval == 0.25


@pytest.fixture
def task() -> ToolTask:
    client = Client(FastMCP())
    return ToolTask(client=client, task_id="t1", tool_name="echo")


def _status(poll_interval: int | None) -> GetTaskResult:
    return GetTaskResult(
        task_id="t1",
        status="working",
        created_at="2026-01-01T00:00:00+00:00",
        last_updated_at="2026-01-01T00:00:00+00:00",
        ttl=None,
        poll_interval=poll_interval,
    )


@pytest.mark.parametrize("poll_interval", [2000, 30_000])
def test_advertised_interval_is_used_verbatim_without_backoff(
    task: ToolTask, poll_interval: int
):
    """An advertised interval is the delay itself, not a ceiling to ramp toward."""
    task._status_cache = _status(poll_interval)
    expected = poll_interval / 1000

    backoff = MIN_POLL_INTERVAL
    for _ in range(5):
        delay, backoff = task._next_poll_delay(backoff)
        assert delay == expected


def test_large_advertised_interval_is_honored(task: ToolTask):
    task._status_cache = _status(24 * 60 * 60 * 1000)
    delay, _ = task._next_poll_delay(MIN_POLL_INTERVAL)
    assert delay == 24 * 60 * 60


@pytest.mark.parametrize("poll_interval", [0, -1, -5000])
def test_non_positive_advertised_interval_is_floored(
    task: ToolTask, poll_interval: int
):
    """A buggy or hostile server must not be able to spin the client."""
    task._status_cache = _status(poll_interval)
    delay, _ = task._next_poll_delay(MIN_POLL_INTERVAL)
    assert delay == MIN_POLL_INTERVAL


def test_unadvertised_interval_ramps_up_to_setting(task: ToolTask):
    task._status_cache = _status(None)
    with temporary_settings(client_task_poll_interval=0.5):
        delays = []
        backoff = MIN_POLL_INTERVAL
        for _ in range(7):
            delay, backoff = task._next_poll_delay(backoff)
            delays.append(delay)

    assert delays == [0.02, 0.04, 0.08, 0.16, 0.32, 0.5, 0.5]


def test_missing_status_cache_ramps_from_floor(task: ToolTask):
    delay, backoff = task._next_poll_delay(MIN_POLL_INTERVAL)
    assert delay == MIN_POLL_INTERVAL
    assert backoff == MIN_POLL_INTERVAL * 2
