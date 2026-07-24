import abc
import contextlib
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import httpx2
import mcp_types
from mcp import ClientSession
from mcp.client.extension import NotificationBinding, ResultClaim
from mcp.client.session import (
    ElicitationFnT,
    ListRootsFnT,
    LoggingFnT,
    MessageHandlerFnT,
    SamplingFnT,
)
from typing_extensions import TypedDict, Unpack

# TypeVar for preserving specific ClientTransport subclass types
ClientTransportT = TypeVar("ClientTransportT", bound="ClientTransport")


class ClientSessionKwargs(TypedDict, total=False):
    """Keyword arguments for the MCP ClientSession constructor."""

    read_timeout_seconds: float | None
    sampling_callback: SamplingFnT | None
    sampling_capabilities: mcp_types.SamplingCapability | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    elicitation_callback: ElicitationFnT | None
    message_handler: MessageHandlerFnT | None
    client_info: mcp_types.Implementation | None
    notification_bindings: Sequence[NotificationBinding[Any]] | None
    extensions: dict[str, dict[str, Any]] | None
    result_claims: Mapping[str, Sequence[ResultClaim[Any]]] | None


@dataclass(frozen=True)
class TransportOptions:
    """How one client wants its connection built.

    These belong to the client rather than to the transport, so a transport
    shared between clients doesn't leak one client's settings to another.

    Attributes:
        session_class: The ClientSession class to instantiate. Proxies supply a
            session that skips output-schema validation, since they relay
            results rather than consume them.
        forward_incoming_headers: Whether to forward the inbound request's
            authorization header upstream. Only appropriate for proxies, where
            the caller's credentials are meant to be propagated. Honored by the
            HTTP and SSE transports; ignored by the others.
        backend_mode: The connect `mode` to give backend clients that a wrapping
            transport builds on this client's behalf, so a chain of connections
            speaks one protocol era end to end. `None` leaves each backend
            client at its own default. Honored by `MCPConfigTransport`, whose
            multi-server form mounts a proxy per configured server; ignored by
            transports that connect to a single backend directly, since those
            carry the connecting client's own session and era.
    """

    session_class: type[ClientSession] = ClientSession
    forward_incoming_headers: bool = False
    backend_mode: str | None = None


# SessionKwargs stays exactly the ClientSession constructor's parameters, so a
# transport can splat it into ClientSession without filtering.
SessionKwargs = ClientSessionKwargs


class ClientTransport(abc.ABC):
    """
    Abstract base class for different MCP client transport mechanisms.

    A Transport is responsible for establishing and managing connections
    to an MCP server, and providing a ClientSession within an async context.

    """

    #: Whether this transport can only carry the legacy (handshake) protocol era.
    #: The modern `2026-07-28` era is sessionless and served over Streamable HTTP;
    #: the SSE transport predates it and cannot serve it. When True, a client with
    #: `mode="auto"` negotiates the legacy handshake directly rather than probing
    #: `server/discover` (which some servers answer over SSE but then cannot serve).
    legacy_only: bool = False

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        """
        Establishes a connection and yields an active ClientSession.

        The ClientSession is *not* expected to be initialized in this context manager.

        The session is guaranteed to be valid only within the scope of the
        async context manager. Connection setup and teardown are handled
        within this context.

        Args:
            transport_options: How the connecting client wants this connection
                               built. Defaults apply when omitted. A transport
                               that wraps others must pass this along.
            **session_kwargs: Keyword arguments to pass to the ClientSession
                              constructor (e.g., callbacks, timeouts).

        Yields:
            A mcp.ClientSession instance.
        """
        raise NotImplementedError
        yield  # ty:ignore[invalid-yield]

    def __repr__(self) -> str:
        # Basic representation for subclasses
        return f"<{self.__class__.__name__}>"

    async def close(self):  # noqa: B027
        """Close the transport."""

    def get_session_id(self) -> str | None:
        """Get the session ID for this transport, if available."""
        return None

    def _set_auth(self, auth: httpx2.Auth | Literal["oauth"] | str | None):
        if auth is not None:
            raise ValueError("This transport does not support auth")
