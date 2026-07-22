"""Stateless session state: the scoped-state vocabulary and the session codec.

This module provides the security core for cross-request state on modern
(stateless) MCP connections. It has two parts:

- `Scope`: the storage-isolation vocabulary shared by `ctx.get_state` /
  `ctx.set_state`. `REQUEST` is today's per-connection state, `USER` keys off the
  authenticated principal, and `SESSION` keys off a sealed session handle.
- `SessionCodec`: mints and verifies the sealed session handle. A handle binds a
  server-minted `session_id` to the authenticated principal (when present) and an
  expiry, sealed with the SDK's AES-GCM request-state codec. Unlike the SDK's
  per-request `requestState` seal, a session handle binds **principal + expiry
  only**, so it survives across different tool calls rather than a single round.

The `Session()` annotation and the request boundary that unseals the handle and
binds it onto the request context live in a later increment. This module only
provides the primitives and the context slot they populate.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import logging
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache
from typing import (
    TYPE_CHECKING,
    Annotated,
    Final,
    NoReturn,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import uuid4

from mcp.server.auth.provider import principal_components
from mcp.server.request_state import InvalidRequestState, RequestStateSecurity

from fastmcp.exceptions import FastMCPError
from fastmcp.server.dependencies import get_access_token, get_context

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger: logging.Logger = logging.getLogger(__name__)

# Default lifetime of a sealed session handle. Session handles must outlive a
# single request (they are threaded through many tool calls), so this defaults
# far above the SDK request-state codec's 600s per-round default. Configurable
# per `SessionCodec`.
DEFAULT_SESSION_TTL: Final[float] = 3600.0

# Envelope version stamped into every sealed payload. The AES-GCM codec already
# binds its own "v1." wire prefix into the seal; this is the *payload* schema
# version, independent of the codec's token format.
_ENVELOPE_VERSION: Final[int] = 1


class Scope(enum.Enum):
    """Storage isolation for `ctx.get_state` / `ctx.set_state`.

    The scope selects both the storage key prefix and the isolation guarantee:

    - `REQUEST`: per-request connection state. Dies with the request on a modern
      (stateless) connection; persists across requests on a stateful one. This is
      the historical default and the only scope that works without auth or a
      session handle.
    - `SESSION`: keyed by `(principal, session_id)` where `session_id` comes from
      a verified sealed handle bound onto the request context. Sealed and
      principal-bound when authenticated; unforgeable (but not cross-client
      isolated) when unauthenticated.
    - `USER`: keyed by the authenticated principal; spans every session for that
      user. Requires authentication.
    """

    REQUEST = "request"
    SESSION = "session"
    USER = "user"


@dataclass(frozen=True)
class SessionIdentity:
    """The verified identity carried by a sealed session handle.

    `principal` is the authenticated `(client_id, issuer, subject)` triple encoded
    as a compact JSON string, or `None` when the handle was minted on an
    unauthenticated connection.
    """

    session_id: str
    principal: str | None


class SessionError(FastMCPError):
    """Base class for session-state errors."""


class InvalidSessionToken(SessionError):
    """A sealed session handle failed verification.

    Raised for foreign, expired, tampered, or malformed handles. The rejection
    reason is captured on `.reason` and logged, never placed on the public
    message, so a boundary that surfaces this to the wire cannot leak *why* a
    handle was rejected (mirroring the SDK request-state boundary's stance).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("invalid or expired session token")


class NoActiveSessionError(SessionError):
    """`Scope.SESSION` state was accessed with no verified session on the request.

    Modern (stateless) connections carry no protocol session, so a client must
    obtain a sealed handle (via `create_session`) and thread it into later calls
    for `SESSION`-scoped state to resolve.
    """

    def __init__(
        self,
        message: str = (
            "no active session — call `create_session` and pass its token as the "
            "session argument to access Scope.SESSION state"
        ),
    ) -> None:
        super().__init__(message)


# The verified session identity for the current request. A later increment's
# boundary sets this after unsealing the handle argument; scoped-state reads it.
_active_session_identity: ContextVar[SessionIdentity | None] = ContextVar(
    "fastmcp_active_session_identity", default=None
)


@contextmanager
def bind_session_identity(identity: SessionIdentity) -> Iterator[SessionIdentity]:
    """Bind a verified session identity onto the current request context.

    Used by the session boundary (and tests) to make `Scope.SESSION` state
    resolvable for the duration of a request.
    """
    token: Token[SessionIdentity | None] = _active_session_identity.set(identity)
    try:
        yield identity
    finally:
        _active_session_identity.reset(token)


def get_active_session_identity() -> SessionIdentity | None:
    """Return the verified session identity bound to the current request, if any."""
    return _active_session_identity.get()


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

    Hashing keeps arbitrary principal strings from injecting the `:` key
    delimiter and bounds the key length. `None` (unauthenticated) collapses to a
    single shared `anon` segment.
    """
    if principal is None:
        return "anon"
    digest = hashlib.sha256(principal.encode("utf-8", "surrogatepass")).hexdigest()
    return digest


def user_state_key(principal: str, key: str) -> str:
    """Storage key for `Scope.USER` state: keyed by principal."""
    return f"user:{_principal_segment(principal)}:{key}"


def session_state_key(identity: SessionIdentity, key: str) -> str:
    """Storage key for `Scope.SESSION` state: keyed by `(principal, session_id)`."""
    return f"session:{_principal_segment(identity.principal)}:{identity.session_id}:{key}"


def session_index_key(identity: SessionIdentity) -> str:
    """Storage key for a session's key index.

    `Scope.SESSION` writes record their user key here so a session's state can be
    enumerated and cleared (by `end_session`) without relying on the store's
    optional key-enumeration protocol — the base `AsyncKeyValue` contract only
    guarantees get/put/delete. A distinct `session-index:` prefix keeps the index
    from ever colliding with a `session:...` state key.
    """
    return f"session-index:{_principal_segment(identity.principal)}:{identity.session_id}"


class _Unset:
    """Sentinel: `unseal` should read the principal from the request context."""


_UNSET: Final[_Unset] = _Unset()


class SessionCodec:
    """Mints and verifies sealed session handles.

    A handle is an opaque, URL-safe token minted server-side. Its payload —
    `{session_id, principal, exp}` — is sealed with the SDK's AES-GCM request-state
    codec, so a client can neither read nor forge it. On use the codec unseals it,
    verifies it has not expired, and — when the request carries an authenticated
    principal — verifies the handle's principal matches. Foreign, expired, or
    tampered handles raise `InvalidSessionToken`.

    Reuse the server's `request_state_security` key policy so session handles and
    the SDK's `requestState` share one key ring; when the server sets no policy,
    `from_security(None)` mints an ephemeral per-process key (single-process safe,
    matching the SDK boundary's default).
    """

    def __init__(
        self,
        security: RequestStateSecurity,
        *,
        ttl: float = DEFAULT_SESSION_TTL,
    ) -> None:
        if not (math.isfinite(ttl) and ttl > 0):
            raise ValueError(f"session ttl must be a positive finite number, got {ttl!r}")
        # Reuse only the raw AES-GCM codec (seal/unseal over bytes). The policy's
        # ttl / audience / bind_principal belong to the SDK's per-request boundary
        # and deliberately do NOT govern session handles.
        self._codec = security.codec
        self._ttl = ttl

    @classmethod
    def from_security(
        cls,
        security: RequestStateSecurity | None,
        *,
        ttl: float = DEFAULT_SESSION_TTL,
    ) -> SessionCodec:
        """Build a codec from a server's request-state policy, or an ephemeral key.

        Pass the server's configured `request_state_security` to bind session
        handles to the same shared key ring. Pass `None` (no server policy) to
        seal under a per-process ephemeral key.
        """
        if security is None:
            security = RequestStateSecurity.ephemeral()
        return cls(security, ttl=ttl)

    def seal(self, session_id: str, principal: str | None) -> str:
        """Mint a sealed handle binding `session_id` to `principal` with an expiry.

        `principal` is the compact-JSON principal string (see `current_principal`)
        or `None` on an unauthenticated connection.
        """
        payload = {
            "v": _ENVELOPE_VERSION,
            "sid": session_id,
            "p": principal,
            "exp": time.time() + self._ttl,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return self._codec.seal(raw)

    def unseal(
        self,
        token: str,
        *,
        request_principal: str | None | _Unset = _UNSET,
    ) -> SessionIdentity:
        """Verify a sealed handle and return its identity.

        Checks integrity (AES-GCM), expiry, and — when the request carries an
        authenticated principal — that the handle's principal matches it. A handle
        minted under a principal but replayed on a request with a *different* or
        *absent* principal is rejected, as is one minted without a principal but
        presented under authentication (principal drift, mirroring the SDK
        boundary).

        By default the request principal is read from the current auth context;
        tests may pass `request_principal` explicitly to exercise the match logic
        without an auth context.

        Raises:
            InvalidSessionToken: Malformed, tampered, expired, or foreign handle.
        """
        if isinstance(request_principal, _Unset):
            request_principal = current_principal()

        try:
            raw = self._codec.unseal(token)
        except InvalidRequestState as exc:
            self._reject(str(exc))

        try:
            claims = json.loads(raw)
            version = claims["v"]
            session_id = claims["sid"]
            principal = claims["p"]
            exp = claims["exp"]
        except (ValueError, KeyError, TypeError):
            self._reject("malformed")

        if version != _ENVELOPE_VERSION or not isinstance(session_id, str):
            self._reject("malformed")
        if principal is not None and not isinstance(principal, str):
            self._reject("malformed")

        now = time.time()
        # Stated positively so a NaN exp fails the comparison and rejects.
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or not (now < exp):
            self._reject("expired")

        if request_principal is not None:
            if principal is None or not hmac.compare_digest(principal, request_principal):
                self._reject("principal mismatch")
        elif principal is not None:
            # Handle bound to a principal, replayed on an unauthenticated request:
            # a downgrade that would leak a user's session. Reject.
            self._reject("principal drift")

        return SessionIdentity(session_id=session_id, principal=principal)

    def _reject(self, reason: str) -> NoReturn:
        """Log the real reason and raise a handle with a generic public message."""
        logger.warning("session token rejected: %s", reason)
        raise InvalidSessionToken(reason)


@dataclass(frozen=True)
class Session:
    """Marks a tool parameter as carrying a sealed session handle.

    Use as `Annotated[str, Session()]` on a tool parameter. The parameter appears
    in the tool's input schema as a plain string — the client supplies the token
    it received from `create_session`. Before the tool body runs, the framework
    unseals and verifies the token (integrity, expiry, and principal binding when
    authenticated) and binds the resulting `SessionIdentity` onto the request, so
    `ctx.get_state(scope=Scope.SESSION)` resolves for the duration of the call.

    A missing, malformed, expired, or foreign token fails the call with
    `InvalidSessionToken` before the body runs — session access never silently
    degrades.

    The tool body receives the raw token string in the annotated parameter. That
    value is rarely needed: session state is read and written through
    `ctx.get_state` / `ctx.set_state` with `Scope.SESSION`, and the annotation's
    job is to establish identity. Declaring the parameter is enough to require a
    valid session for the call.
    """


@lru_cache(maxsize=5000)
def session_parameter_names(fn: Callable[..., object]) -> tuple[str, ...]:
    """Names of a function's parameters annotated with `Session()`.

    Scans the resolved type hints for `Annotated[..., Session()]` metadata.
    Returns an empty tuple when the hints cannot be resolved (the function then
    simply carries no session boundary).
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
        if any(isinstance(meta, Session) for meta in get_args(hint)[1:]):
            names.append(name)
    return tuple(names)


def create_session() -> str:
    """Create a new session and return its opaque, sealed handle token.

    Mint a fresh `session_id`, bind it to the request's authenticated principal
    (if any) and an expiry, and return the sealed token as a plain string. The
    client stores this token and passes it back as the `Session()` argument on
    later calls to access `Scope.SESSION` state.
    """
    ctx = get_context()
    return ctx.fastmcp.session_codec.seal(str(uuid4()), current_principal())


async def end_session(session: Annotated[str, Session()]) -> str:
    """End a session and delete all of its `Scope.SESSION` state.

    The `Session()` boundary verifies the token before this body runs, so an
    invalid or foreign token is rejected up front. All state written under this
    session is removed; subsequent reads return their defaults.
    """
    ctx = get_context()
    await ctx.clear_session_state()
    return "session ended"


class SessionProvider:
    """Opt-in lifecycle for sealed-handle session state.

    Wire onto a server with `FastMCP(session_provider=SessionProvider(...))`. It
    registers two tools:

    - `create_session` mints a sealed handle and returns it as a plain string.
    - `end_session` verifies a handle and clears that session's state.

    The provider does not own storage: session state lives in the server's
    configured state store (`FastMCP(session_state_store=...)`), and handles are
    sealed with the server's `session_codec`, which reuses the server's
    `request_state_security` key ring (or a per-process ephemeral key when none is
    configured). `ttl` sets how long a minted handle stays valid, threaded into
    the server's `SessionCodec`.
    """

    def __init__(self, *, ttl: float = DEFAULT_SESSION_TTL) -> None:
        if not (math.isfinite(ttl) and ttl > 0):
            raise ValueError(
                f"session ttl must be a positive finite number, got {ttl!r}"
            )
        self.ttl = ttl

    def register(self, server: FastMCP) -> None:
        """Register the `create_session` / `end_session` tools on `server`."""
        server.add_tool(create_session)
        server.add_tool(end_session)
