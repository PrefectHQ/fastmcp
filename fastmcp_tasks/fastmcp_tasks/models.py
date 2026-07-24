"""SEP-2663 tasks-extension wire models.

The `io.modelcontextprotocol/tasks` extension (SEP-2663) defines its own wire
shapes, distinct from the SEP-1686 task types the MCP SDK still ships
(`mcp_types.Task` uses `ttl`/`pollInterval`; SEP-2663 uses `ttlMs`/`pollIntervalMs`
and a *flat* `CreateTaskResult` rather than a nested `{task: ...}`). These models
serialize to the SEP-2663 shapes and are validated against the vendored draft
JSON schema in the test suite.

A note on `_meta`: the draft schema composes result shapes as
`allOf[Result, Task]`, and the `Task` arm carries `additionalProperties: false`
without listing `_meta`. A `_meta` key therefore fails schema validation on those
results. These models leave `_meta` unset and rely on the runner's
`exclude_none=True` dump to omit it, so serialized instances validate cleanly.
`ttlMs` is required-but-nullable in the schema; in practice the engine always
emits a numeric value (Docket carries a default execution TTL), so the
`exclude_none` dump never drops it.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp_types import RequestParams, Result
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "TaskStatus",
    "CreateTaskResult",
    "GetTaskResult",
    "UpdateTaskResult",
    "CancelTaskResult",
    "GetTaskParams",
    "UpdateTaskParams",
    "CancelTaskParams",
    "GetTaskRequest",
    "UpdateTaskRequest",
    "CancelTaskRequest",
    "missing_capability_error_data",
]

#: JSON-RPC error code for "Missing Required Client Capability" (SEP-2663). A
#: tool whose task mode is `required` returns this when the client did not opt
#: the tasks extension in for the request.
MISSING_REQUIRED_CLIENT_CAPABILITY = -32003

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]


class _TaskFields(BaseModel):
    """The flat task fields shared by every SEP-2663 task result shape.

    Serializes to the schema's `Task` object (camelCase aliases, `ttlMs`
    required-but-nullable). No `_meta`: the schema's `additionalProperties:
    false` on the task arm forbids it (see module docstring).
    """

    # Serialization aliases: the engine constructs these by field name and the
    # runner dumps them to camelCase (`model_dump(by_alias=True)`). The
    # claim-production wrap returns that dump unchanged, so no input alias is
    # needed.
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(serialization_alias="taskId")
    status: TaskStatus
    created_at: str = Field(serialization_alias="createdAt")
    last_updated_at: str = Field(serialization_alias="lastUpdatedAt")
    ttl_ms: float | None = Field(serialization_alias="ttlMs")
    status_message: str | None = Field(
        default=None, serialization_alias="statusMessage"
    )
    poll_interval_ms: float | None = Field(
        default=None, serialization_alias="pollIntervalMs"
    )


class CreateTaskResult(_TaskFields):
    """Result of an augmented `tools/call` that the server ran as a task.

    A flat merge of `Result` and `Task` (SEP-2663): the finished task stub the
    client polls with `tasks/get`. Status is typically `working`.

    `resultType` is the wire discriminator that distinguishes this from a
    `CallToolResult` on the shared `tools/call` method: the modern result union
    carries a required `resultType`, and the SDK's client-side `ResultClaim`
    for tasks requires this model to pin it to `Literal["task"]`. The vendored
    draft schema omits `resultType` from the task arm (its
    `additionalProperties: false` forbids it) — a schema-vs-protocol
    contradiction reported upstream. Protocol interop requires the field, so we
    emit it; only this shape needs it (the `tasks/*` methods each have a single
    result type and bypass the discriminated union).
    """

    result_type: Literal["task"] = Field(
        default="task", serialization_alias="resultType"
    )


class GetTaskResult(_TaskFields):
    """Result of `tasks/get`: the detailed task (`Result & DetailedTask`).

    Carries exactly one of `result` (completed), `error` (failed), or
    `input_requests` (input_required) alongside the flat task fields, matching
    the schema's 5-status union. The three payload fields default to `None` and
    are dropped from the wire dump for the statuses that do not use them.

    `resultType` is `"complete"` (SEP-2663 L338): `tasks/get` itself completes
    normally, whatever the task's own status. As with `CreateTaskResult`, the
    draft schema's `additionalProperties: false` omits this field — a
    contradiction reported upstream; protocol interop requires emitting it.
    """

    result_type: Literal["complete"] = Field(
        default="complete", serialization_alias="resultType"
    )
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = Field(
        default=None, serialization_alias="inputRequests"
    )


class UpdateTaskResult(Result):
    """Acknowledgement for `tasks/update` (SEP-2663 `Result`, `resultType: "complete"`)."""

    result_type: Literal["complete"] = Field(
        default="complete", serialization_alias="resultType"
    )


class CancelTaskResult(Result):
    """Acknowledgement for `tasks/cancel` (SEP-2663 `Result`, `resultType: "complete"`)."""

    result_type: Literal["complete"] = Field(
        default="complete", serialization_alias="resultType"
    )


class GetTaskParams(RequestParams):
    """Params for `tasks/get` / `tasks/cancel`: the target task id."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")


# `tasks/cancel` params are identical to `tasks/get` (just `taskId`).
CancelTaskParams = GetTaskParams


class UpdateTaskParams(RequestParams):
    """Params for `tasks/update`: task id plus the caller's input responses."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    input_responses: dict[str, Any] = Field(alias="inputResponses")


class GetTaskRequest(BaseModel):
    """`tasks/get` request envelope (used by tests and clients)."""

    model_config = ConfigDict(populate_by_name=True)

    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskParams


class UpdateTaskRequest(BaseModel):
    """`tasks/update` request envelope."""

    model_config = ConfigDict(populate_by_name=True)

    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskParams


class CancelTaskRequest(BaseModel):
    """`tasks/cancel` request envelope."""

    model_config = ConfigDict(populate_by_name=True)

    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: GetTaskParams


def missing_capability_error_data() -> dict[str, Any]:
    """Build the `data.requiredCapabilities` payload for a -32003 error.

    A `required`-mode tool called without the client opting the tasks extension
    in for the request returns this so the client learns which capability to
    declare.
    """
    from fastmcp.utilities.tasks import TASKS_EXTENSION_ID

    return {"requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}}
