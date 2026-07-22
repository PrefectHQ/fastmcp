"""Stateless session state: server-side per-user and per-session storage.

Modern (2026-07-28) MCP connections are stateless by construction — every
request builds a fresh connection whose in-memory state is discarded when the
request returns. This module gives tools two explicit ways to keep state across
calls, both backed by the server's existing state store and both isolated by the
authenticated principal rather than by any client-declared identifier.

- `Session`: async `get`/`set`/`delete`/`clear` over a single dict stored under
  one key, scoped to a `(principal, session_id)` pair. This is the state-accessor
  object a handler works with — the value returned by `ctx.get_session(id)` and
  injected for a `UserSession` parameter.
- `session: UserSession` (injected): a per-user bucket, dependency-injected like
  `ctx: Context` and keyed by the request's authenticated principal. Requires
  auth. `UserSession` is the injection annotation; the injected value is a
  `Session`.
- `session_id: SessionId` (argument): a required string the agent supplies,
  resolved with `ctx.get_session(session_id)`. Works with or without auth, but
  only isolates between callers under auth. Declaring one auto-wires the default
  `SessionProvider` (its `create_session` / `end_session` tools) unless the
  developer already registered one or opted out.
- `SessionProvider`: a `Provider` contributing `create_session` / `end_session`
  tools. Registered automatically when a tool declares `session_id: SessionId`,
  or explicitly via `add_provider`.

Isolation is the authenticated principal, not the session id. State keyed by
`(principal, session_id)` means a request under principal B can never address
principal A's keys, no matter what `session_id` it passes; the id only organizes
sessions within a principal. Without auth there is no principal wall — a session
id is a bearer capability and sessions are not a boundary between clients.
"""

from __future__ import annotations

import hashlib
import json
import weakref
from collections.abc import Callable, Iterable, Sequence
from functools import lru_cache
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import uuid4

from mcp.server.auth.provider import principal_components
from uncalled_for import Dependency

from fastmcp.exceptions import FastMCPError
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.server.providers.base import Provider

if TYPE_CHECKING:
    from key_value.aio.adapters.pydantic import PydanticAdapter

    from fastmcp.server.server import FastMCP, StateValue
    from fastmcp.tools.base import Tool


# The description the framework auto-populates onto a `SessionId` argument so an
# agent reading the tool schema learns the create-then-pass contract with no
# hand-prompting.
SESSION_ID_DESCRIPTION: Final[str] = (
    "Session identifier. Call `create_session` to obtain one, then pass it here "
    "to persist state across calls in the same session."
)


class SessionAuthError(FastMCPError):
    """An injected `session: UserSession` was requested with no authenticated principal.

    Per-user session injection keys off the request's authenticated principal, so
    it is only meaningful under auth. A tool that needs cross-call state without
    auth should take a `session_id: SessionId` argument instead.
    """

    def __init__(
        self,
        message: str = (
            "Injected `session: UserSession` requires an authenticated principal, "
            "but this request is unauthenticated. Use a `session_id: SessionId` "
            "argument for cross-call state on unauthenticated connections."
        ),
    ) -> None:
        super().__init__(message)


def current_principal() -> str | None:
    """The authenticated principal for the current request as a compact JSON string.

    Returns the `(client_id, issuer, subject)` triple encoded as compact JSON, or
    `None` on an unauthenticated request. Two users of one OAuth client are
    distinct principals whenever the token verifier supplies a subject.
    """
    token = get_access_token()
    if token is None:
        return None
    return json.dumps(principal_components(token), separators=(",", ":"))


def _principal_segment(principal: str | None) -> str:
    """A fixed-length, delimiter-safe key segment for a principal.

    Hashing keeps an arbitrary principal string from injecting the `:` key
    delimiter and bounds the key length. `None` (unauthenticated) collapses to a
    single shared `anon` segment — without a principal there is no isolation wall.
    """
    if principal is None:
        return "anon"
    return hashlib.sha256(principal.encode("utf-8", "surrogatepass")).hexdigest()


def session_storage_key(principal: str | None, session_id: str) -> str:
    """The single storage key holding a session's state dict.

    Keyed by `(principal, session_id)`: the principal is the isolation wall, the
    id organizes sessions within it. A session's whole state lives under this one
    key as a dict, so one key means one store TTL per session and `clear` is a
    single delete.
    """
    return f"session:{_principal_segment(principal)}:{session_id}"


class Session:
    """Async accessors over one `(principal, session_id)` bucket of state.

    A session's state is a single dict stored under one key. `get`/`set`/`delete`
    read-modify-write that dict; `clear` deletes the key. Writes never impose a
    TTL — retention is entirely the server store's (configure it on the store you
    pass to `FastMCP(session_state_store=...)`).

    Concurrent writes to one session race on the read-modify-write; session state
    is small and typically driven serially by one agent, so this is acceptable.
    """

    def __init__(
        self,
        *,
        store: PydanticAdapter[StateValue],
        principal: str | None,
        session_id: str,
    ) -> None:
        self._store = store
        self._principal = principal
        self._session_id = session_id
        self._key = session_storage_key(principal, session_id)

    async def _load(self) -> dict[str, Any]:
        """Read the session's state dict, or an empty dict when unset."""
        result = await self._store.get(key=self._key)
        if result is None:
            return {}
        value = result.value
        return dict(value) if isinstance(value, dict) else {}

    async def _save(self, data: dict[str, Any]) -> None:
        """Write the session's state dict back under its single key (no TTL)."""
        from fastmcp.server.server import StateValue

        await self._store.put(key=self._key, value=StateValue(value=data))

    async def get(self, key: str, default: Any = None) -> Any:
        """Return the value for `key`, or `default` when it is not set."""
        data = await self._load()
        return data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        """Store `value` under `key` in this session (read-modify-write)."""
        data = await self._load()
        data[key] = value
        await self._save(data)

    async def delete(self, key: str) -> None:
        """Remove `key` from this session, if present."""
        data = await self._load()
        if key in data:
            del data[key]
            await self._save(data)

    async def clear(self) -> None:
        """Delete the entire session — its one key and all of its state."""
        await self._store.delete(key=self._key)


class UserSession(Session):
    """Annotation marker for the injected per-user session.

    A `session: UserSession` parameter is **dependency-injected** like
    `ctx: Context`: keyed by the request's authenticated principal, excluded from
    the input schema, and requiring auth (it raises `SessionAuthError` with no
    principal). It is the injection *annotation* — the value a handler receives is
    an ordinary `Session`, so `await session.get(...)`, `.set`, `.delete`, and
    `.clear` all work exactly as on any other `Session`.

    Use `UserSession` when one state bucket per user is what you want, with
    nothing for the agent to pass. For distinct sessions the agent selects, take a
    `session_id: SessionId` argument instead.

    ```python
    from fastmcp.server.sessions import UserSession

    @mcp.tool
    async def remember(fact: str, session: UserSession) -> str:
        await session.set("fact", fact)
        return "noted"
    ```

    Subclasses `Session` only so the framework's type-based injection detector can
    key off it; it adds no behavior and is never instantiated directly.
    """


class _SessionIdMarker:
    """Metadata marker identifying a `SessionId`-annotated parameter."""


# A `session_id: SessionId` parameter is a plain required string in the input
# schema (the agent supplies it); the marker lets the framework recognize it and
# auto-populate its description with the create-then-pass contract.
SessionId = Annotated[str, _SessionIdMarker()]


@lru_cache(maxsize=5000)
def session_id_parameter_names(fn: Callable[..., object]) -> tuple[str, ...]:
    """Names of a function's parameters annotated with `SessionId`.

    Scans resolved type hints for `Annotated[str, _SessionIdMarker()]` metadata.
    Returns an empty tuple when the hints cannot be resolved (the function then
    simply carries no auto-populated session-id description).
    """
    try:
        hints = get_type_hints(fn, include_extras=True)
    except (TypeError, NameError):
        return ()
    names: list[str] = []
    for name, hint in hints.items():
        if name == "return":
            continue
        if get_origin(hint) is not Annotated:
            continue
        if any(isinstance(meta, _SessionIdMarker) for meta in get_args(hint)[1:]):
            names.append(name)
    return tuple(names)


class _CurrentSession(Dependency["Session"]):
    """Dependency that injects a per-user `Session` keyed by the request principal.

    Mirrors `_CurrentContext`: a `session: UserSession` parameter is rewritten to
    default to this dependency, so it is excluded from the input schema and
    resolved at call time. Raises `SessionAuthError` when the request carries no
    authenticated principal.
    """

    async def __aenter__(self) -> Session:
        principal = current_principal()
        if principal is None:
            raise SessionAuthError
        ctx = get_context()
        return Session(
            store=ctx.fastmcp._state_store,
            principal=principal,
            session_id=principal,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def CurrentSession() -> Session:
    """Inject the per-user `Session` for the current authenticated principal.

    Rarely written explicitly — a `session: UserSession` parameter is rewritten
    to this. Provided for parity with `CurrentContext()` when an explicit default
    is preferred.
    """
    return cast("Session", _CurrentSession())


async def create_session() -> str:
    """Create a new session and return its identifier.

    Mints an unguessable `uuid4` and returns it as a string. Store it and pass it
    back as a `session_id` argument on later calls to persist state across a
    session. State is keyed by the authenticated principal, so the id organizes
    sessions within a user; on an unauthenticated connection the id is the only
    thing standing between callers, which is why it is unguessable.
    """
    return str(uuid4())


async def end_session(session_id: SessionId) -> str:
    """End a session and delete all of its state."""
    await get_context().get_session(session_id).clear()
    return "session ended"


class SessionProvider(Provider):
    """Opt-in provider contributing the session lifecycle tools.

    Add it like any other provider:

    ```python
    from fastmcp.server.sessions import SessionProvider

    mcp.add_provider(SessionProvider())
    ```

    It registers two tools:

    - `create_session()` mints an unguessable `uuid4` and returns it.
    - `end_session(session_id)` clears that session's state.

    It owns no storage (session state lives in the server's configured
    `session_state_store`) and imposes no TTL (retention is the store's). It
    exists only to hand out unguessable ids — a tool can take a `session_id`
    argument and accept any caller-supplied id without it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tools: list[Tool] | None = None

    async def _list_tools(self) -> Sequence[Tool]:
        if self._tools is None:
            from fastmcp.tools.base import Tool

            self._tools = [
                Tool.from_function(create_session),
                Tool.from_function(end_session),
            ]
        return self._tools


def tools_declare_session_id(tools: Iterable[Tool]) -> bool:
    """Whether any tool in `tools` declares a `session_id: SessionId` parameter.

    Checks each function-backed tool's resolved hints for the `SessionId` marker.
    Used to decide whether the default `SessionProvider` should be auto-registered.
    """
    from fastmcp.tools.function_tool import FunctionTool

    return any(
        isinstance(tool, FunctionTool) and session_id_parameter_names(tool.fn)
        for tool in tools
    )


class _ImplicitSessionProvider(SessionProvider):
    """Server-owned `SessionProvider` that activates only when it is needed.

    Consulted directly by the server's tool listing/lookup (it is deliberately
    kept out of the public `providers` list so it never perturbs provider indices
    or user introspection). It contributes its `create_session` / `end_session`
    tools only when every condition holds:

    - the server has not opted out (`FastMCP(auto_session_provider=False)`),
    - no `SessionProvider` was registered explicitly (a manual one wins), and
    - at least one locally registered tool declares a `session_id: SessionId`
      argument.

    Detection scans the server's own registered tools (the `LocalProvider`'s
    decorator / `add_tool` set), which is read fresh at list time — so activation
    is independent of the order in which tools and providers are registered (a
    `session_id` tool added after construction still activates it) and never
    invokes another provider's listing (a mounted sub-server wires its own
    `SessionProvider` and is not scanned here, so its middleware is never run as a
    side effect of this decision).
    """

    def __init__(self, server: FastMCP) -> None:
        super().__init__()
        self._server_ref = weakref.ref(server)

    async def _list_tools(self) -> Sequence[Tool]:
        server = self._server_ref()
        if server is None or not server._auto_session_provider:
            return []
        if any(isinstance(p, SessionProvider) for p in server.providers):
            return []
        local_tools = await server._local_provider._list_tools()
        if not tools_declare_session_id(local_tools):
            return []
        return await super()._list_tools()
