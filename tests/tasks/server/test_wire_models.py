"""Validate the SEP-2663 wire models against the vendored draft JSON schema.

The models in `fastmcp_tasks.models` serialize to the `io.modelcontextprotocol/tasks`
extension shapes. This suite validates a serialized instance of each result shape
against the corresponding `$defs` entry in the vendored draft schema
(`tests/fixtures/ext-tasks-schema-draft.json`), so wire drift is caught here.

The vendored schema composes results as `allOf[Result, Task]` where the Task arm
carries `additionalProperties: false`; a stray `_meta` therefore fails
validation. The models omit `_meta` and the runner's `exclude_none` dump keeps it
out, which is exactly what these assertions check.

**Known schema-vs-protocol contradiction:** the modern `tools/call` result union
carries a required `resultType` discriminator, and the SDK's client-side
`ResultClaim` requires `CreateTaskResult` to pin `resultType: "task"` — so we
emit it. The draft schema's Task arm, however, forbids `resultType` (its
`additionalProperties: false` does not list it). We validate the task *fields*
against the schema with the discriminator stripped, and assert separately that
the discriminator is present on the wire. This contradiction is reported
upstream (the schema forbids a field the base protocol requires).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp_tasks.models import (
    CancelTaskResult,
    CreateTaskResult,
    GetTaskResult,
    TaskStatus,
    UpdateTaskResult,
)
from jsonschema import Draft202012Validator

_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "ext-tasks-schema-draft.json").read_text()
)
_DEFS = _SCHEMA["$defs"]

_ISO = "2026-07-21T12:00:00+00:00"


def _validate(def_name: str, instance: dict[str, Any]) -> None:
    schema = {"$defs": _DEFS, **_DEFS[def_name]}
    Draft202012Validator(schema).validate(instance)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


def _dump_task_fields(model: Any) -> dict[str, Any]:
    """Dump without the `resultType` discriminator the draft schema omits.

    `resultType` is required by the protocol's result union but forbidden by the
    schema's Task arm; strip it so the remaining task fields can be validated
    against the schema. `test_create_task_result_emits_result_type_discriminator`
    covers the discriminator itself.
    """
    dumped = _dump(model)
    dumped.pop("resultType", None)
    return dumped


def test_create_task_result_matches_schema():
    result = CreateTaskResult(
        task_id="t1",
        status="working",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        poll_interval_ms=5000,
    )
    _validate("CreateTaskResult", _dump_task_fields(result))


def test_create_task_result_emits_result_type_discriminator():
    """The protocol requires `resultType: "task"` to distinguish a tasked result.

    The modern `tools/call` union discriminates on `resultType`, and the SDK's
    `ResultClaim` for tasks pins the model to `Literal["task"]`; without it a
    client cannot tell a task result from a `CallToolResult`.
    """
    result = CreateTaskResult(
        task_id="t1",
        status="working",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
    )
    assert _dump(result)["resultType"] == "task"


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        ("working", {}),
        ("completed", {"result": {"content": [], "isError": False}}),
        ("failed", {"error": {"code": -32603, "message": "boom"}}),
        (
            "input_required",
            {
                "input_requests": {
                    "k1": {"method": "elicitation/create", "params": {"message": "?"}}
                }
            },
        ),
        ("cancelled", {}),
    ],
)
def test_get_task_result_matches_schema(status: TaskStatus, payload: dict[str, Any]):
    result = GetTaskResult(
        task_id="t1",
        status=status,
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        poll_interval_ms=5000,
        **payload,
    )
    _validate("GetTaskResult", _dump_task_fields(result))


def test_get_task_result_completed_omits_error_and_inputs():
    """A completed result carries only `result` (the union arm forbids the rest)."""
    result = GetTaskResult(
        task_id="t1",
        status="completed",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        result={"content": [], "isError": False},
    )
    dumped = _dump(result)
    assert "error" not in dumped
    assert "inputRequests" not in dumped


def test_null_ttl_is_permitted_by_schema():
    """`ttlMs` is required-but-nullable; a null TTL still validates."""
    result = CreateTaskResult(
        task_id="t1",
        status="working",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=None,
    )
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=False)
    # Drop the other None optionals the runner would also drop, keeping ttlMs=null,
    # and the resultType the draft schema omits (see module docstring).
    dumped = {k: v for k, v in dumped.items() if v is not None or k == "ttlMs"}
    dumped.pop("resultType", None)
    _validate("CreateTaskResult", dumped)


@pytest.mark.parametrize("model", [UpdateTaskResult(), CancelTaskResult()])
def test_ack_results_match_schema(model: Any):
    def_name = type(model).__name__
    _validate(def_name, _dump(model))
