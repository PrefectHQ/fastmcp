"""Stateless session state: server-side per-user and per-session storage.

Modern (2026-07-28) MCP connections are stateless by construction — every
request builds a fresh connection whose in-memory state is discarded when the
request returns. This module gives tools two explicit ways to keep state across
calls, both backed by the server's existing state store and both isolated by the
authenticated principal rather than by any client-declared identifier.

- `Session`: async `get`/`set`/`delete`/`clear` over a single dict stored under
  one key, scoped to a `(principal, session_id)` pair. This is the state-accessor
  object a handler works with — the value the standalone `get_session(id)`
  returns and the value injected for a `UserSession` parameter.
- `session: UserSession` (injected): a per-user bucket, dependency-injected like
  `ctx: Context` and keyed by the request's authenticated principal. Requires
  auth. `UserSession` is the injection annotation; the injected value is a
  `Session`. It is always available under auth — no `create_session`, no
  provider, no validation.
- `session_id: SessionId` (argument): a required string the agent supplies,
  resolved with the standalone `await get_session(session_id)`. The id is
  minted
  by `create_session`; an id that was never created (or was created under a
  different principal) is rejected. This validation is the whole guarantee — an
  unminted id never resolves, so nothing enforces provider registration.
- `SessionProvider`: a `Provider` contributing `create_session` / `end_session`
  tools. Register it with `mcp.add_provider(SessionProvider())` so a tool that
  takes `session_id` has a way to mint ids; without it, no id can be created, so
  those tools simply cannot resolve a session.

Isolation is the authenticated principal, not the session id. State keyed by
`(principal, session_id)` means a request under principal B can never address
principal A's keys, no matter what `session_id` it passes; the id only organizes
sessions within a principal. Without auth there is no principal wall — a session
id is a bearer capability and sessions are not a boundary between clients.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import time
from collections.abc import Callable, Sequence
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
from fastmcp.server.dependencies import get_access_token, get_server, get_session
from fastmcp.server.providers.base import Provider
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from key_value.aio.adapters.pydantic import PydanticAdapter

    from fastmcp.server.server import StateValue
    from fastmcp.tools.base import Tool

logger = get_logger(__name__)


# The description the framework auto-populates onto a `SessionId` argument so an
# agent reading the tool schema learns the create-then-pass contract with no
# hand-prompting.
# Deliberately names no specific tool. The session-creation tool can be renamed
# by composition — mounting a server under a namespace exposes it as, e.g.,
# `child_create_session` — so hard-coding a tool name here would point agents at
# a tool that does not exist under that mount. Describing the capability keeps
# the contract correct regardless of how the lifecycle tool is named.
SESSION_ID_DESCRIPTION: Final[str] = (
    "Session identifier. Use a tool to create a session, then pass the resulting "
    "id here to persist state across calls in the same session."
)

# Reserved top-level keys in a session's stored dict. User state lives under
# `_STATE_KEY` (a sub-dict), and `_MARKER_KEY` records that the session was
# created. Keeping user state in a sub-dict means normal `set`/`delete`/`clear`
# can never collide with or clobber the creation marker, so a created session
# stays distinguishable from a missing one — including after `clear()`, which
# empties the sub-dict but leaves the marker in place.
_MARKER_KEY: Final[str] = "_created"
_STATE_KEY: Final[str] = "state"

# Fixed session-id suffix for the injected per-user bucket. The principal is
# already hashed into the key's namespace segment (`_principal_segment`), which
# alone makes the bucket unique per user — using the *raw* principal again as
# the id suffix would embed unhashed identity data (issuer, client id, subject)
# in the storage key and in any logs that record it. A reserved constant avoids
# that while a `create_session`-minted uuid4 can never collide with it.
_USER_SESSION_ID: Final[str] = "_user"


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


class InvalidSession(FastMCPError):
    """A session id did not resolve to a session created under the current principal.

    Raised by `get_session(session_id)` when the id was never created, or was
    created under a different principal. The public message is deliberately
    generic — the specific reason (which id, which principal) is logged at debug
    level, not returned to the caller, so an attacker cannot distinguish "unknown
    id" from "belongs to someone else".
    """

    def __init__(self, message: str = "Invalid or unknown session.") -> None:
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
    key as a dict, so one key means one store TTL per session and `end` is a
    single delete.
    """
    return f"session:{_principal_segment(principal)}:{session_id}"


class Session:
    """Async accessors over one `(principal, session_id)` bucket of state.

    A session's state is a single dict stored under one key. That dict holds user
    state in a `state` sub-dict and a small creation marker alongside it, so a
    created-but-empty session is still distinguishable from a missing one.
    `get`/`set`/`delete` read-modify-write the sub-dict; `clear` empties the
    sub-dict but keeps the session valid; `end` deletes the whole key. Writes
    never impose a TTL — retention is entirely the server store's (configure it on
    the store you pass to `FastMCP(session_state_store=...)`).

    Concurrent writes to one session race on the read-modify-write; session state
    is small and typically driven serially by one agent, so this is acceptable.
    """

    def __init__(
        self,
        *,
        store: PydanticAdapter[StateValue],
        principal: str | None,
        session_id: str,
        public_id: str | None = None,
    ) -> None:
        self._store = store
        self._principal = principal
        self._session_id = session_id
        self._public_id = public_id
        self._key = session_storage_key(principal, session_id)

    @property
    def id(self) -> str | None:
        """The session's identifier, or `None` for an injected per-user session.

        For a session resolved from a `session_id` argument (or minted by
        `create_session`) this is that id. An injected `UserSession` has no
        distinct id — its bucket is the authenticated user — so it is `None`; the
        internal principal-derived key is deliberately not exposed here.
        """
        return self._public_id

    async def _load_raw(self) -> dict[str, Any] | None:
        """Read the session's full stored dict, or `None` when the key is unset."""
        result = await self._store.get(key=self._key)
        if result is None:
            return None
        value = result.value
        return dict(value) if isinstance(value, dict) else None

    async def _save_raw(self, data: dict[str, Any]) -> None:
        """Write the session's full dict back under its single key (no TTL)."""
        from fastmcp.server.server import StateValue

        await self._store.put(key=self._key, value=StateValue(value=data))

    @staticmethod
    def _state_of(raw: dict[str, Any] | None) -> dict[str, Any]:
        """The user-state sub-dict of a raw stored dict (empty when absent)."""
        if raw is None:
            return {}
        state = raw.get(_STATE_KEY)
        return dict(state) if isinstance(state, dict) else {}

    async def _exists(self) -> bool:
        """Whether a session record exists for this `(principal, session_id)`.

        True only once `create_session` has written the creation marker. A raw
        store entry without the marker (e.g. an injected `UserSession` bucket) is
        not a created session and does not satisfy this check.
        """
        raw = await self._load_raw()
        return raw is not None and _MARKER_KEY in raw

    async def _create(self) -> None:
        """Write the initial record so the session exists (called by `create_session`)."""
        raw = await self._load_raw() or {}
        raw[_MARKER_KEY] = time.time()
        raw.setdefault(_STATE_KEY, {})
        await self._save_raw(raw)

    async def get(self, key: str, default: Any = None) -> Any:
        """Return the value for `key`, or `default` when it is not set."""
        raw = await self._load_raw()
        return self._state_of(raw).get(key, default)

    async def set(self, key: str, value: Any) -> None:
        """Store `value` under `key` in this session (read-modify-write).

        Preserves the creation marker: only the user-state sub-dict is touched.
        """
        raw = await self._load_raw() or {}
        state = self._state_of(raw)
        state[key] = value
        raw[_STATE_KEY] = state
        await self._save_raw(raw)

    async def delete(self, key: str) -> None:
        """Remove `key` from this session, if present (preserves the marker)."""
        raw = await self._load_raw()
        if raw is None:
            return
        state = self._state_of(raw)
        if key in state:
            del state[key]
            raw[_STATE_KEY] = state
            await self._save_raw(raw)

    async def clear(self) -> None:
        """Empty the session's user state but keep the session valid.

        The user-state sub-dict is reset to empty while the creation marker stays
        in place, so a cleared session still resolves through `get_session`.
        To invalidate a session entirely, use `end` (what `end_session` calls).
        """
        raw = await self._load_raw()
        if raw is None:
            return
        raw[_STATE_KEY] = {}
        await self._save_raw(raw)

    async def end(self) -> None:
        """Invalidate the session — delete its one key and all of its state.

        After this the id no longer resolves through `get_session`. This is
        what `end_session` calls; `clear` only empties state and keeps the session.
        """
        await self._store.delete(key=self._key)


class UserSession(Session):
    """Annotation marker for the injected per-user session.

    A `session: UserSession` parameter is **dependency-injected** like
    `ctx: Context`: keyed by the request's authenticated principal, excluded from
    the input schema, and requiring auth (it raises `SessionAuthError` with no
    principal). It doubles as the injection *annotation* and the injected
    type — the value a handler receives is a `UserSession`, which subclasses
    `Session`, so `await session.get(...)`, `.set`, `.delete`, and `.clear` all
    work exactly as on any other `Session`.

    Unlike `session_id: SessionId`, the per-user bucket needs no `create_session`,
    no `SessionProvider`, and no validation — it is always available under auth,
    keyed directly by the caller's identity.

    ```python
    from fastmcp.server.sessions import UserSession

    @mcp.tool
    async def remember(fact: str, session: UserSession) -> str:
        await session.set("fact", fact)
        return "noted"
    ```

    Subclasses `Session` only so the framework's type-based injection detector can
    key off it; it adds no behavior of its own.
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

    `functools.partial` is unwrapped first, since `get_type_hints` rejects a
    partial object — FastMCP supports registering a partial as a tool, and its
    schema is still built from the underlying function, so its `SessionId`
    parameters must be detected here too. Parameters the partial has already
    bound — positionally or by keyword — are dropped, matching the tool's actual
    argument surface (the partial's own signature already reflects this).
    """
    target: object = fn
    while isinstance(target, functools.partial):
        target = target.func
    if not callable(target):
        return ()
    try:
        hints = get_type_hints(target, include_extras=True)
    except (TypeError, NameError):
        return ()
    # `inspect.signature` on the (possibly partial) callable reports only the
    # parameters still open to callers — a partial's bound positional and keyword
    # arguments are already removed — so it is the source of truth for the tool's
    # argument surface. Fall back to accepting every hinted name if the signature
    # cannot be read.
    try:
        remaining = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        remaining = None
    names: list[str] = []
    for name, hint in hints.items():
        if name == "return" or (remaining is not None and name not in remaining):
            continue
        if get_origin(hint) is not Annotated:
            continue
        if any(isinstance(meta, _SessionIdMarker) for meta in get_args(hint)[1:]):
            names.append(name)
    return tuple(names)


def _current_user_session() -> UserSession | None:
    """Build the per-user session for the current principal, or `None` if unauth.

    Resolves the store through `get_server()` rather than `get_context()`: a
    `task=True` tool whose only injected dependency is `UserSession` runs in a
    Docket worker with no foreground context, and `get_server()` is task-aware (it
    resolves via the task-server map in a worker).
    """
    principal = current_principal()
    if principal is None:
        return None
    return UserSession(
        store=get_server()._state_store,
        principal=principal,
        session_id=_USER_SESSION_ID,
    )


class _CurrentSession(Dependency["Session"]):
    """Dependency that injects a per-user `Session` keyed by the request principal.

    Mirrors `_CurrentContext`: a `session: UserSession` parameter is rewritten to
    default to this dependency, so it is excluded from the input schema and
    resolved at call time. Raises `SessionAuthError` when the request carries no
    authenticated principal.
    """

    async def __aenter__(self) -> Session:
        session = _current_user_session()
        if session is None:
            raise SessionAuthError
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _OptionalCurrentSession(Dependency["Session | None"]):
    """Dependency for an *optional* per-user session (`session: UserSession | None`).

    Mirrors `_OptionalCurrentContext`: when the request carries no authenticated
    principal it injects `None` instead of raising, so a handler that declares the
    parameter optional (default `None`) can run on unauthenticated requests and
    branch on whether a session is available.
    """

    async def __aenter__(self) -> Session | None:
        return _current_user_session()

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


def OptionalCurrentSession() -> Session | None:
    """Inject the per-user `Session`, or `None` when the request is unauthenticated.

    Rarely written explicitly — a `session: UserSession | None = None` parameter
    is rewritten to this. Provided for parity with `OptionalCurrentContext()`.
    """
    return cast("Session | None", _OptionalCurrentSession())


async def create_session() -> str:
    """Create a new session and return its identifier.

    Mints an unguessable `uuid4`, records an initial session owned by the current
    principal, and returns the id as a string. Store it and pass it back as a
    `session_id` argument on later calls to persist state across a session — only
    an id created this way resolves. State is keyed by the authenticated
    principal, so the id organizes sessions within a user; on an unauthenticated
    connection the id is the only thing standing between callers, which is why it
    is unguessable.
    """
    session_id = str(uuid4())
    session = Session(
        store=get_server()._state_store,
        principal=current_principal(),
        session_id=session_id,
        public_id=session_id,
    )
    await session._create()
    return session_id


async def end_session(session_id: SessionId) -> str:
    """End a session and delete all of its state.

    Validates the id like any other resolution (an unknown or foreign id is
    rejected), then deletes the session's key so the id no longer resolves.
    """
    session = await get_session(session_id)
    await session.end()
    return "session ended"


class SessionProvider(Provider):
    """Provider contributing the session lifecycle tools.

    Register it whenever a tool declares a `session_id: SessionId` argument:

    ```python
    from fastmcp.server.sessions import SessionProvider

    mcp.add_provider(SessionProvider())
    ```

    It registers two tools:

    - `create_session()` mints an unguessable `uuid4`, records the session, and
      returns the id.
    - `end_session(session_id)` invalidates that session and deletes its state.

    It owns no storage (session state lives in the server's configured
    `session_state_store`) and imposes no TTL (retention is the store's). It
    exists to mint and end owned session ids. Registration is not enforced: with
    no provider, no id can be created, so every `get_session(...)` rejects —
    a `session_id` tool without a provider simply cannot resolve a session.
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
