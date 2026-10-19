"""The server-side claim-production wrap for the tasks extension.

`wire_production` widens the SDK's `tools/call` result serialization so a
`CreateTaskResult` (`resultType: "task"`) survives to the wire instead of being
stripped by the `CallToolResult | InputRequiredResult` surface. These tests
exercise the wrap at the exact boundary the server runner calls
(`mcp_types.methods.serialize_server_result`), which is where the SDK otherwise
drops the task fields.
"""

from __future__ import annotations

import mcp_types.methods as methods
import pytest
from fastmcp_tasks import wire_production

_MODERN = "2026-07-28"

_TASK_DICT = {
    "resultType": "task",
    "taskId": "abc123",
    "status": "working",
    "createdAt": "2026-07-21T12:00:00+00:00",
    "lastUpdatedAt": "2026-07-21T12:00:00+00:00",
    "ttlMs": 900000,
}


@pytest.fixture
def installed():
    """Install the wrap for one test, guaranteeing removal."""
    wire_production.install()
    try:
        yield
    finally:
        wire_production.uninstall()


def test_without_wrap_task_fields_are_stripped():
    """Baseline: the stock serializer drops the task fields (the gap we close)."""
    out = methods.serialize_server_result("tools/call", _MODERN, dict(_TASK_DICT))
    assert "taskId" not in out


def test_wrap_preserves_task_result(installed):
    out = methods.serialize_server_result("tools/call", _MODERN, dict(_TASK_DICT))
    assert out["taskId"] == "abc123"
    assert out["resultType"] == "task"
    assert out["status"] == "working"


def test_wrap_leaves_ordinary_tool_result_untouched(installed):
    """A normal (non-task) tools/call result serializes exactly as before."""
    complete = {"content": [{"type": "text", "text": "hi"}], "resultType": "complete"}
    out = methods.serialize_server_result("tools/call", _MODERN, complete)
    assert out["content"] == [{"type": "text", "text": "hi"}]
    assert "taskId" not in out


def test_wrap_delegates_non_diverted_calls(installed):
    """Only a task-tagged tools/call is diverted; everything else delegates.

    A `tools/list` call is never routed to task production, so its payload is
    validated by the stock `ListToolsResult` surface exactly as without the
    wrap — proven here by the stock validator rejecting an off-surface dict
    rather than the wrap silently converting or swallowing it.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        methods.serialize_server_result("tools/list", _MODERN, {"tools": []})


def test_uninstall_restores_stock_serializer():
    wire_production.install()
    wrapped = methods.serialize_server_result
    wire_production.uninstall()
    assert methods.serialize_server_result is not wrapped
    # And the task fields are stripped again once restored.
    out = methods.serialize_server_result("tools/call", _MODERN, dict(_TASK_DICT))
    assert "taskId" not in out


def test_refcount_survives_nested_holds():
    """Two holds (sibling extensions): the wrap stays until the last release."""
    wire_production.install()
    wire_production.install()
    wire_production.uninstall()
    # One hold remains; the wrap is still active.
    out = methods.serialize_server_result("tools/call", _MODERN, dict(_TASK_DICT))
    assert out["taskId"] == "abc123"
    wire_production.uninstall()
    # Last hold released; stock behavior restored.
    out = methods.serialize_server_result("tools/call", _MODERN, dict(_TASK_DICT))
    assert "taskId" not in out
