"""Per-task Redis state for SEP-2663 end-and-reenter input gathering.

A background task gathers client input by *ending a leg* and re-entering, never
by blocking a worker. When a `task=True` tool returns an `InputRequiredResult`,
the leg's Docket execution completes and the worker is freed; the task's state
lives here in Redis as `input_required`. When the client answers via
`tasks/update`, a fresh Docket execution (the next leg) re-runs the tool with the
accumulated state injected onto its `Context`. No worker ever waits for input.

This module owns the durable state each task carries between legs:

- **args** — the original tool arguments, re-supplied to every leg.
- **current_leg / leg** — the latest leg's Docket execution key and its number.
- **request_state** — the opaque string a leg carried forward (SEP-2322).
- **input_responses** — the typed answers the last `tasks/update` delivered,
  translated to the tool's own request keys.
- **input:requests / input:map** — the current leg's outstanding requests, keyed
  by a server-minted surfaced key, plus the surfaced-key → tool-key mapping.

Each surfaced request key is minted fresh with high-entropy suffix and never
reused after its response is delivered (SEP-2663 L350): a task that asks twice,
or a leg that requests several inputs at once, surfaces distinct, independently
answerable keys, and the tool reads its *own* keys on the next leg via the
translated `input_responses`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any, cast

import mcp_types

from fastmcp_tasks.keys import task_redis_prefix

if TYPE_CHECKING:
    from docket import Docket

logger = logging.getLogger(__name__)

# How long a task's input state (outstanding requests and delivered responses)
# lives before expiring. With end-and-reenter no worker is held while a task is
# input_required, so this bounds only how long durable input state survives, not
# any worker slot.
INPUT_TTL_SECONDS = 3600

# Reconstruct a typed response from its stored `{"type": name, "data": dump}`
# form so a re-entered leg reads a real `ElicitResult` (etc.) on
# `ctx.input_responses`, matching the foreground guard contract.
_RESULT_TYPE_BY_NAME: dict[str, type[mcp_types.Result]] = {
    "ElicitResult": mcp_types.ElicitResult,
    "CreateMessageResult": mcp_types.CreateMessageResult,
    "CreateMessageResultWithTools": mcp_types.CreateMessageResultWithTools,
    "ListRootsResult": mcp_types.ListRootsResult,
}

# Map an outstanding request's wire method to the result type its answer
# validates into. Elicitation is the supported in-task input; the others are
# kept complete so a client that answers one is parsed rather than dropped.
_RESULT_TYPE_BY_METHOD: dict[str, type[mcp_types.Result]] = {
    "elicitation/create": mcp_types.ElicitResult,
    "sampling/createMessage": mcp_types.CreateMessageResult,
    "roots/list": mcp_types.ListRootsResult,
}


def result_type_for_method(method: str) -> type[mcp_types.Result]:
    """The result type an outstanding request's answer validates into."""
    return _RESULT_TYPE_BY_METHOD.get(method, mcp_types.ElicitResult)


def _prefix(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return f"{task_redis_prefix(task_scope)}:{task_id}"


def _args_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:args")


def _current_leg_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:current_leg")


def _leg_number_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:leg")


def _request_state_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:request_state")


def _input_responses_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:input_responses")


def _requests_key(
    docket: Docket, task_scope: str | None, task_id: str, leg: int
) -> str:
    """Redis hash of a leg's outstanding input requests, keyed by surfaced key.

    Scoped by leg number so a re-entered leg's fresh requests never collide with
    the answered leg's stale ones in the shared keyspace.
    """
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:input:{leg}:requests")


def _map_key(docket: Docket, task_scope: str | None, task_id: str, leg: int) -> str:
    """Redis hash mapping a leg's surfaced keys back to the tool's own keys."""
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:input:{leg}:map")


def _mint_surfaced_key(task_id: str) -> str:
    """Mint a unique surfaced key for one outstanding request (SEP-2663 L350).

    Namespaced by the task id and suffixed with fresh entropy so no two
    requests — across legs or within one leg — ever collide, and a key is never
    reused after its response is delivered.
    """
    return f"{task_id}:{secrets.token_hex(8)}"


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


# ---------------------------------------------------------------------------
# Task arguments and leg pointer (written at create, advanced at tasks/update)
# ---------------------------------------------------------------------------


async def save_task_args(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    arguments: dict[str, Any],
    ttl_seconds: int,
) -> None:
    """Store the original tool arguments, re-supplied to every leg."""
    async with docket.redis() as redis:
        await redis.set(
            _args_key(docket, task_scope, task_id),
            json.dumps(arguments),
            ex=ttl_seconds,
        )


async def load_task_args(
    docket: Docket, task_scope: str | None, task_id: str
) -> dict[str, Any]:
    """Load the stored tool arguments for a task's next leg."""
    async with docket.redis() as redis:
        raw = await redis.get(_args_key(docket, task_scope, task_id))
    decoded = _decode(raw)
    if not decoded:
        return {}
    parsed = json.loads(decoded)
    return parsed if isinstance(parsed, dict) else {}


async def save_current_leg(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    leg_key: str,
    leg_number: int,
    ttl_seconds: int,
) -> None:
    """Record the latest leg's Docket execution key and its number."""
    async with docket.redis() as redis:
        await redis.set(
            _current_leg_key(docket, task_scope, task_id), leg_key, ex=ttl_seconds
        )
        await redis.set(
            _leg_number_key(docket, task_scope, task_id),
            str(leg_number),
            ex=ttl_seconds,
        )


async def refresh_current_leg_ttl(
    docket: Docket, task_scope: str | None, task_id: str, ttl_seconds: int
) -> None:
    """Extend the current-leg pointer's TTL (sliding expiration).

    The pointer is written with a wall-clock TTL, but a leg's execution can run
    longer than that — a resumed guard leg especially. Refreshing on each poll
    keeps the routing pointer alive for an actively-polled task no matter how
    long the leg runs, so ``_lookup_task`` never falls back to the base leg
    while the current leg is still executing.
    """
    async with docket.redis() as redis:
        await redis.expire(_current_leg_key(docket, task_scope, task_id), ttl_seconds)
        await redis.expire(_leg_number_key(docket, task_scope, task_id), ttl_seconds)


async def load_current_leg(
    docket: Docket, task_scope: str | None, task_id: str
) -> tuple[str | None, int]:
    """Return the current leg's execution key and number (defaults to 1)."""
    async with docket.redis() as redis:
        leg_key = _decode(
            await redis.get(_current_leg_key(docket, task_scope, task_id))
        )
        leg_raw = _decode(await redis.get(_leg_number_key(docket, task_scope, task_id)))
    try:
        leg_number = int(leg_raw) if leg_raw else 1
    except ValueError:
        leg_number = 1
    return leg_key, leg_number


# ---------------------------------------------------------------------------
# Outstanding requests (written by the capture wrapper, read by tasks/get)
# ---------------------------------------------------------------------------


async def store_outstanding(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    leg: int,
    serialized_requests: dict[str, dict[str, Any]],
    request_state: str | None,
    ttl_seconds: int = INPUT_TTL_SECONDS,
) -> None:
    """Persist a leg's outstanding input requests plus its carried state.

    ``serialized_requests`` maps the tool's own request keys to serialized
    ``InputRequest`` payloads. Each is stored under a freshly minted surfaced
    key, with the surfaced-key → tool-key mapping recorded alongside so
    ``tasks/update`` can translate answers back. ``request_state`` is written
    when the leg carried one and cleared otherwise, so it travels to the next
    leg verbatim.
    """
    requests_key = _requests_key(docket, task_scope, task_id, leg)
    map_key = _map_key(docket, task_scope, task_id, leg)
    state_key = _request_state_key(docket, task_scope, task_id)

    async with docket.redis() as redis:
        for tool_key, payload in serialized_requests.items():
            surfaced = _mint_surfaced_key(task_id)
            await redis.hset(requests_key, surfaced, json.dumps(payload))
            await redis.hset(map_key, surfaced, tool_key)
        await redis.expire(requests_key, ttl_seconds)
        await redis.expire(map_key, ttl_seconds)
        if request_state is not None:
            await redis.set(state_key, request_state, ex=ttl_seconds)
        else:
            await redis.delete(state_key)


async def read_outstanding_inputs(
    docket: Docket, task_scope: str | None, task_id: str, leg: int
) -> dict[str, Any]:
    """Return a leg's outstanding input requests, keyed by surfaced key.

    Empty when the leg is not waiting on input. Consumed by ``tasks/get`` to
    build the ``input_required`` status and its ``inputRequests`` snapshot.
    """
    async with docket.redis() as redis:
        raw = await redis.hgetall(_requests_key(docket, task_scope, task_id, leg))
    outstanding: dict[str, Any] = {}
    for key, value in raw.items():
        key_str = _decode(key)
        value_str = _decode(value)
        if key_str is None or value_str is None:
            continue
        try:
            outstanding[key_str] = json.loads(value_str)
        except json.JSONDecodeError:
            continue
    return outstanding


async def _read_outstanding_map(
    docket: Docket, task_scope: str | None, task_id: str, leg: int
) -> dict[str, str]:
    """Return the surfaced-key → tool-key mapping for a leg."""
    async with docket.redis() as redis:
        raw = await redis.hgetall(_map_key(docket, task_scope, task_id, leg))
    mapping: dict[str, str] = {}
    for key, value in raw.items():
        key_str = _decode(key)
        value_str = _decode(value)
        if key_str is None or value_str is None:
            continue
        mapping[key_str] = value_str
    return mapping


# ---------------------------------------------------------------------------
# Responses (written by tasks/update, read by the next leg's context factory)
# ---------------------------------------------------------------------------


async def translate_responses(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    leg: int,
    responses: dict[str, Any],
) -> dict[str, mcp_types.Result] | None:
    """Translate a ``tasks/update`` payload into typed, tool-keyed responses.

    ``responses`` is keyed by the surfaced keys the client received for ``leg``.
    Unknown or already-satisfied keys are ignored (SEP-2663). Each recognized
    answer is validated into the result type its request maps to and re-keyed to
    the tool's own request key. Returns ``None`` when nothing matched, so the
    caller can treat a stale or empty update as an idempotent no-op.
    """
    outstanding = await read_outstanding_inputs(docket, task_scope, task_id, leg)
    if not outstanding:
        return None
    mapping = await _read_outstanding_map(docket, task_scope, task_id, leg)

    translated: dict[str, mcp_types.Result] = {}
    for surfaced_key, raw in responses.items():
        payload = outstanding.get(surfaced_key)
        if payload is None:
            continue
        tool_key = mapping.get(surfaced_key)
        if tool_key is None:
            continue
        method = payload.get("method", "elicitation/create")
        result_type = result_type_for_method(method)
        translated[tool_key] = result_type.model_validate(raw)

    return translated or None


async def store_input_responses(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    translated: dict[str, mcp_types.Result],
    ttl_seconds: int = INPUT_TTL_SECONDS,
) -> None:
    """Store translated responses for the next leg to read via ``ctx``.

    The responses are stored typed-but-serialized (``{"type", "data"}``) so the
    next leg's context factory reconstructs real result objects keyed by the
    tool's own request keys.
    """
    stored = {
        tool_key: {
            "type": type(result).__name__,
            "data": result.model_dump(by_alias=True, mode="json"),
        }
        for tool_key, result in translated.items()
    }
    async with docket.redis() as redis:
        await redis.set(
            _input_responses_key(docket, task_scope, task_id),
            json.dumps(stored),
            ex=ttl_seconds,
        )


async def clear_outstanding(
    docket: Docket, task_scope: str | None, task_id: str, leg: int
) -> None:
    """Drop a leg's outstanding requests and mapping once it has been answered.

    The answered surfaced keys are never reused (a later leg mints its own), so
    a duplicate ``tasks/update`` naming them finds nothing and is a no-op.
    """
    async with docket.redis() as redis:
        await redis.delete(_requests_key(docket, task_scope, task_id, leg))
        await redis.delete(_map_key(docket, task_scope, task_id, leg))


def _cancelled_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:cancelled")


async def mark_cancelled(
    docket: Docket, task_scope: str | None, task_id: str, ttl_seconds: int
) -> None:
    """Record that a task was cancelled at the logical (not per-leg) level.

    An ``input_required`` task's current Docket execution is already
    ``COMPLETED`` — the outstanding-input record is what keeps it parked — so
    ``docket.cancel`` on that execution is a no-op. This durable marker lets
    ``tasks/get`` report ``cancelled`` and ``tasks/update`` refuse to resume,
    regardless of the underlying execution state. Expires with the task's TTL.
    """
    async with docket.redis() as redis:
        await redis.set(
            _cancelled_key(docket, task_scope, task_id), b"1", ex=max(1, ttl_seconds)
        )


async def is_cancelled(docket: Docket, task_scope: str | None, task_id: str) -> bool:
    """Whether the task was logically cancelled (see ``mark_cancelled``)."""
    async with docket.redis() as redis:
        return bool(await redis.exists(_cancelled_key(docket, task_scope, task_id)))


# How long the per-task update lock lives if its holder dies mid-update. A
# generous ceiling: a single tasks/update is fast, so the lock is normally held
# for milliseconds; the TTL only guards against a crashed holder.
_UPDATE_LOCK_TTL_SECONDS = 30


def _update_lock_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    return docket.key(f"{_prefix(docket, task_scope, task_id)}:update_lock")


async def acquire_update_lock(
    docket: Docket, task_scope: str | None, task_id: str
) -> bool:
    """Take the per-task update lock, or return False if one is already held.

    Serializes concurrent ``tasks/update`` calls for a task so two racing
    answers cannot each enqueue a next leg (double execution). A well-behaved
    client polls sequentially and never contends; a loser is an idempotent
    no-op, matching SEP-2663's "ignore already-satisfied" rule.
    """
    async with docket.redis() as redis:
        got = await redis.set(
            _update_lock_key(docket, task_scope, task_id),
            b"1",
            nx=True,
            ex=_UPDATE_LOCK_TTL_SECONDS,
        )
    return bool(got)


async def acquire_update_lock_blocking(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    *,
    timeout: float = 5.0,
    poll: float = 0.02,
) -> bool:
    """Wait for the per-task update lock, up to ``timeout`` seconds.

    ``tasks/cancel`` uses this to serialize with an in-flight ``tasks/update``:
    it must not cancel a stale leg while an update concurrently enqueues the
    next one. A single update is fast (milliseconds), so contention is brief;
    returns False if the lock is still held at the deadline (a wedged holder),
    letting the caller proceed best-effort rather than hang.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await acquire_update_lock(docket, task_scope, task_id):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(poll)


async def release_update_lock(
    docket: Docket, task_scope: str | None, task_id: str
) -> None:
    """Release the per-task update lock."""
    async with docket.redis() as redis:
        await redis.delete(_update_lock_key(docket, task_scope, task_id))


async def load_pending_input(
    docket: Docket, task_scope: str | None, task_id: str
) -> tuple[str | None, mcp_types.InputResponses | None]:
    """Load the per-leg state a re-entered leg reads via ``ctx``.

    Returns ``(request_state, input_responses)``: the opaque state carried
    forward and the typed answers keyed by the tool's own request keys. Both are
    ``None`` on the first leg (nothing has been asked yet).
    """
    async with docket.redis() as redis:
        state_raw = _decode(
            await redis.get(_request_state_key(docket, task_scope, task_id))
        )
        responses_raw = _decode(
            await redis.get(_input_responses_key(docket, task_scope, task_id))
        )

    responses: dict[str, mcp_types.Result] | None = None
    if responses_raw:
        parsed = json.loads(responses_raw)
        if isinstance(parsed, dict):
            responses = {}
            for tool_key, entry in parsed.items():
                if not isinstance(entry, dict):
                    continue
                result_type = _RESULT_TYPE_BY_NAME.get(entry.get("type", ""))
                if result_type is None:
                    continue
                responses[tool_key] = result_type.model_validate(entry.get("data"))

    # The reconstructed values are the concrete result types the tool asked for;
    # `InputResponses` is that union keyed by request key. The `Result` element
    # type erases that for the checker, so narrow at the return.
    if responses is None:
        return state_raw, None
    return state_raw, cast("mcp_types.InputResponses", responses)
