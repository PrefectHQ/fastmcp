"""Poll-interval floor/ceiling behavior for client-side task waiting."""

import pytest
from mcp_types import GetTaskResult
from pydantic import ValidationError

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp.client.tasks import MAX_POLL_INTERVAL, MIN_POLL_INTERVAL, ToolTask
from fastmcp.settings import Settings


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


@pytest.mark.parametrize("poll_interval", [0, None])
def test_missing_server_poll_interval_falls_back_to_setting(
    task: ToolTask, poll_interval: int | None
):
    task._status_cache = _status(poll_interval)
    assert task._poll_ceiling_seconds() == fastmcp.settings.client_task_poll_interval


@pytest.mark.parametrize("poll_interval", [1, 5, 19])
def test_sub_floor_server_poll_interval_is_clamped_to_floor(
    task: ToolTask, poll_interval: int
):
    task._status_cache = _status(poll_interval)
    assert task._poll_ceiling_seconds() == MIN_POLL_INTERVAL


def test_excessive_server_poll_interval_is_clamped_to_max(task: ToolTask):
    task._status_cache = _status(24 * 60 * 60 * 1000)
    assert task._poll_ceiling_seconds() == MAX_POLL_INTERVAL


def test_reasonable_server_poll_interval_is_used_as_is(task: ToolTask):
    task._status_cache = _status(2000)
    assert task._poll_ceiling_seconds() == 2.0
