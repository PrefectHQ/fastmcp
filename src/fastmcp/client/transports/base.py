import abc
import contextlib
import datetime
from collections.abc import AsyncIterator
from typing import Literal, TypeVar

import httpx
import mcp.types
from mcp import ClientSession
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


class SessionKwargs(TypedDict, total=False):
    """Keyword arguments for the MCP ClientSession constructor."""

    read_timeout_seconds: datetime.timedelta | None
    sampling_callback: SamplingFnT | None
    sampling_capabilities: mcp.types.SamplingCapability | None
    list_roots_callback: ListRootsFnT | None
    logging_callback: LoggingFnT | None
    elicitation_callback: ElicitationFnT | None
    message_handler: MessageHandlerFnT | None
    client_info: mcp.types.Implementation | None


class ClientTransport(abc.ABC):
    """
    Abstract base class for different MCP client transport mechanisms.

    A Transport is responsible for establishing and managing connections
    to an MCP server, and providing a ClientSession within an async context.

    """

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncIterator[ClientSession]:
        """
        Establishes a connection and yields an active ClientSession.

        The ClientSession is *not* expected to be initialized in this context manager.

        The session is guaranteed to be valid only within the scope of the
        async context manager. Connection setup and teardown are handled
        within this context.

        Args:
            **session_kwargs: Keyword arguments to pass to the ClientSession
                              constructor (e.g., callbacks, timeouts).

        Yields:
            A mcp.ClientSession instance.
        """
        raise NotImplementedError
        yield

    def __repr__(self) -> str:
        # Basic representation for subclasses
        return f"<{self.__class__.__name__}>"

    async def close(self):  # noqa: B027
        """Close the transport."""

    def get_session_id(self) -> str | None:
        """Get the session ID for this transport, if available."""
        return None

    def _set_auth(self, auth: httpx.Auth | Literal["oauth"] | str | None):
        if auth is not None:
            raise ValueError("This transport does not support auth")
