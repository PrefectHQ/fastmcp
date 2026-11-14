"""Events-extension methods for the FastMCP client."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

import anyio
import mcp_types
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.telemetry import client_span
from fastmcp.events._constants import EVENTS_EXTENSION_ID
from fastmcp.events.models import (
    EventOccurrence,
    EventType,
    ListEventsParams,
    ListEventsRequest,
    ListEventsResult,
    PollEventsParams,
    PollEventsRequest,
    PollEventsResult,
)
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client.client import Client

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250
_REMEMBERED_EVENT_IDS = 1024


def _trace_meta() -> mcp_types.RequestParamsMeta | None:
    return cast("mcp_types.RequestParamsMeta | None", inject_trace_context(None))


class EventSubscription:
    """A poll loop over one event type, iterated with `async for`.

    `cursor` is the position after the last poll. Persist it and pass it to
    `subscribe_events(cursor=...)` to resume after a restart. `truncated` is
    set once the server reports that it skipped events the cursor asked for.
    """

    def __init__(
        self,
        client: Client,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        cursor: str | None,
        max_age_ms: int | None,
        max_events: int | None,
        min_poll_interval: float,
    ) -> None:
        self._client = client
        self.name = name
        self.arguments = arguments
        self.cursor = cursor
        self.truncated = False
        self._max_age_ms = max_age_ms
        self._max_events = max_events
        self._min_poll_interval = min_poll_interval
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _is_new(self, event: EventOccurrence) -> bool:
        if event.event_id in self._seen:
            return False
        self._seen[event.event_id] = None
        if len(self._seen) > _REMEMBERED_EVENT_IDS:
            self._seen.popitem(last=False)
        return True

    async def __aiter__(self) -> AsyncIterator[EventOccurrence]:
        while True:
            result = await self._client.poll_events_mcp(
                self.name,
                self.arguments,
                cursor=self.cursor,
                max_age_ms=self._max_age_ms,
                max_events=self._max_events,
            )
            self.cursor = result.cursor
            if result.truncated:
                self.truncated = True
                logger.warning(
                    "[%s] Server skipped events for %r; the subscription has a gap",
                    self._client.name,
                    self.name,
                )
            for event in result.events:
                if self._is_new(event):
                    yield event
            if not result.has_more:
                await anyio.sleep(
                    max(result.next_poll_ms / 1000, self._min_poll_interval)
                )


class ClientEventsMixin:
    """Mixin providing SEP-3415 Events methods for `Client`."""

    def _require_events_extension(self: Client) -> None:
        if self.protocol_version is None:
            raise RuntimeError(
                "Client is not connected. Use the 'async with client:' context "
                "manager first."
            )
        if self.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            raise RuntimeError("The Events extension requires a modern MCP connection")
        client_extensions = self._session_kwargs.get("extensions") or {}
        if EVENTS_EXTENSION_ID not in client_extensions:
            raise RuntimeError(
                "This client did not opt into the Events extension; pass "
                "EventsClientExtension() in Client(extensions=[...])"
            )
        capabilities = self.server_capabilities
        server_extensions = capabilities.extensions if capabilities else None
        if not server_extensions or EVENTS_EXTENSION_ID not in server_extensions:
            raise RuntimeError("The server does not advertise the Events extension")

    async def list_events_mcp(
        self: Client, *, cursor: str | None = None
    ) -> ListEventsResult:
        """Send one `events/list` request and return its protocol result.

        Args:
            cursor: Optional pagination cursor from a previous result.

        Raises:
            RuntimeError: If the client is disconnected or Events was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_events_extension()
        with client_span(
            "events/list",
            "events/list",
            "",
            session_id=self.transport.get_session_id(),
        ):
            request = ListEventsRequest(
                params=ListEventsParams(cursor=cursor, meta=_trace_meta())
            )
            return await self._await_with_session_monitoring(
                self.session.send_request(request, ListEventsResult)
            )

    async def list_events(
        self: Client, max_pages: int = AUTO_PAGINATION_MAX_PAGES
    ) -> list[EventType]:
        """Retrieve every event type the server offers.

        Args:
            max_pages: Maximum number of pages to fetch before raising.

        Raises:
            RuntimeError: If Events was not negotiated or the page limit is reached.
            MCPError: If the server rejects a request.
        """
        events: list[EventType] = []
        cursor: str | None = None
        for _ in range(max_pages):
            result = await self.list_events_mcp(cursor=cursor)
            events.extend(result.events)
            if result.next_cursor is None or result.next_cursor == cursor:
                return events
            cursor = result.next_cursor
        raise RuntimeError(
            f"[{self.name}] Reached auto-pagination limit ({max_pages} pages) "
            "for list_events. Use list_events_mcp() for manual pagination, "
            "or increase max_pages."
        )

    async def poll_events_mcp(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        cursor: str | None = None,
        max_age_ms: int | None = None,
        max_events: int | None = None,
    ) -> PollEventsResult:
        """Send one `events/poll` request and return its protocol result.

        Args:
            name: Event type to poll.
            arguments: Subscription arguments matching the event's input schema.
            cursor: Position from a previous poll. `None` starts from now: the
                server returns no events and a cursor for later polls.
            max_age_ms: Do not replay events older than this.
            max_events: Cap on the number of events returned.

        Raises:
            RuntimeError: If the client is disconnected or Events was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_events_extension()
        with client_span(
            f"events/poll {name}",
            "events/poll",
            name,
            session_id=self.transport.get_session_id(),
        ):
            request = PollEventsRequest(
                params=PollEventsParams(
                    name=name,
                    arguments=arguments,
                    cursor=cursor,
                    max_age_ms=max_age_ms,
                    max_events=max_events,
                    meta=_trace_meta(),
                )
            )
            return await self._await_with_session_monitoring(
                self.session.send_request(request, PollEventsResult)
            )

    def subscribe_events(
        self: Client,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        cursor: str | None = None,
        max_age_ms: int | None = None,
        max_events: int | None = None,
        min_poll_interval: float = 1.0,
    ) -> EventSubscription:
        """Poll one event type and iterate its events as they arrive.

        The loop polls at the interval the server recommends, drains a backlog
        without waiting, and drops events it has already yielded. It runs only
        while iterated, so leaving the `async for` ends the subscription.

        Args:
            name: Event type to subscribe to.
            arguments: Subscription arguments matching the event's input schema.
            cursor: Position to resume from. `None` starts from now.
            max_age_ms: Do not replay events older than this.
            max_events: Cap on the number of events per poll.
            min_poll_interval: Shortest wait between polls, in seconds, whatever
                the server recommends.
        """
        return EventSubscription(
            self,
            name,
            arguments,
            cursor=cursor,
            max_age_ms=max_age_ms,
            max_events=max_events,
            min_poll_interval=min_poll_interval,
        )
