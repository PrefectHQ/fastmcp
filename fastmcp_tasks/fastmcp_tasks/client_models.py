"""Client-side wire models for the SEP-2663 tasks extension.

These mirror the server models in ``models.py`` but flip the alias direction:
the server *produces* the wire (``serialization_alias`` -> camelCase dump), while
the client *consumes* it. The SDK validates both a claimed ``tools/call`` result
and a ``tasks/get`` response with ``model_validate(raw, by_name=False)``, so these
models declare **validation** aliases (``Field(alias="taskId")``) to read the
camelCase wire keys.

``ClientCreateTaskResult`` is the claim shape the tasks ``ResultClaim`` resolves.
It must subclass ``mcp_types.Result`` (not ``CallToolResult`` /
``InputRequiredResult``) and pin ``result_type`` to ``Literal["task"]`` — the
SDK's ``ResultClaim.__post_init__`` enforces exactly this. ``ClientGetTaskResult``
is the typed ``tasks/get`` response: the flat task fields plus exactly one of
``result`` (completed), ``error`` (failed), or ``inputRequests`` (input_required).
"""

from __future__ import annotations

from typing import Any, Literal

import mcp_types
from mcp_types import RequestParams, Result
from pydantic import ConfigDict, Field

__all__ = [
    "TaskStatus",
    "ClientCreateTaskResult",
    "ClientGetTaskResult",
    "GetTaskRequest",
    "GetTaskRequestParams",
    "UpdateTaskRequest",
    "UpdateTaskRequestParams",
    "CancelTaskRequest",
    "CancelTaskRequestParams",
]

TaskStatus = Literal["working", "input_required", "completed", "failed", "cancelled"]


class _ClientTaskFields(Result):
    """The flat task fields shared by every SEP-2663 task result, read from the wire.

    Validation aliases (camelCase) because the SDK validates the server's
    ``model_dump(by_alias=True)`` output with ``by_name=False``.
    """

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    status: TaskStatus
    created_at: str = Field(alias="createdAt")
    last_updated_at: str = Field(alias="lastUpdatedAt")
    ttl_ms: float | None = Field(default=None, alias="ttlMs")
    status_message: str | None = Field(default=None, alias="statusMessage")
    poll_interval_ms: float | None = Field(default=None, alias="pollIntervalMs")


class ClientCreateTaskResult(_ClientTaskFields):
    """The claimed ``tools/call`` result the server returns when it runs a call as a task.

    Pinned to ``resultType: "task"`` so the tasks ``ResultClaim`` can key on it.
    The resolver polls ``tasks/get`` from here to the finished result.
    """

    result_type: Literal["task"] = Field(alias="resultType")


class ClientGetTaskResult(_ClientTaskFields):
    """The typed ``tasks/get`` response: task fields plus the inlined outcome.

    Exactly one of ``result`` / ``error`` / ``input_requests`` is set, matching
    the task's status. ``result_type`` is ``"complete"`` because ``tasks/get``
    itself always completes normally, whatever the task's own status.
    """

    result_type: Literal["complete"] = Field(alias="resultType")
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] | None = Field(default=None, alias="inputRequests")


class GetTaskRequestParams(RequestParams):
    """Params for ``tasks/get`` / ``tasks/cancel``: the target task id.

    These are outbound (client -> server), so they carry *serialization* aliases:
    the client constructs them by field name and `send_request` dumps them to the
    camelCase wire shape with `by_alias=True`.
    """

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(serialization_alias="taskId")


CancelTaskRequestParams = GetTaskRequestParams


class UpdateTaskRequestParams(RequestParams):
    """Params for ``tasks/update``: task id plus the caller's input responses."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(serialization_alias="taskId")
    input_responses: dict[str, Any] = Field(serialization_alias="inputResponses")


class GetTaskRequest(mcp_types.Request[GetTaskRequestParams, Literal["tasks/get"]]):
    """``tasks/get`` request envelope for ``ClientSession.send_request``."""

    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskRequestParams


class UpdateTaskRequest(
    mcp_types.Request[UpdateTaskRequestParams, Literal["tasks/update"]]
):
    """``tasks/update`` request envelope for ``ClientSession.send_request``."""

    method: Literal["tasks/update"] = "tasks/update"
    params: UpdateTaskRequestParams


class CancelTaskRequest(
    mcp_types.Request[CancelTaskRequestParams, Literal["tasks/cancel"]]
):
    """``tasks/cancel`` request envelope for ``ClientSession.send_request``."""

    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: CancelTaskRequestParams
