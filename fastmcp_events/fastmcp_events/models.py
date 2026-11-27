"""Wire models for the MCP Events extension (SEP-3415).

Experimental: this API may change in any release without a deprecation period.
"""

from __future__ import annotations

from typing import Any, Literal

import mcp_types
from pydantic import BaseModel, ConfigDict, Field


class EventType(BaseModel):
    """An event type a server offers, as listed by `events/list`."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    delivery: list[Literal["poll", "push", "webhook"]] = Field(min_length=1)
    input_schema: dict[str, Any] | None = Field(default=None, alias="inputSchema")
    payload_schema: dict[str, Any] | None = Field(default=None, alias="payloadSchema")
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class EventOccurrence(BaseModel):
    """One delivered event."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    name: str
    timestamp: str
    data: dict[str, Any]
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class ListEventsParams(mcp_types.PaginatedRequestParams):
    """Parameters for `events/list`."""


class PollEventsParams(mcp_types.RequestParams):
    """Parameters for `events/poll`: one subscription per request."""

    name: str
    arguments: dict[str, Any] | None = None
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    max_events: int | None = Field(default=None, ge=1)


class ListEventsResult(mcp_types.PaginatedResult):
    """Result of `events/list`."""

    events: list[EventType]
    result_type: Literal["complete"] = "complete"


class PollEventsResult(mcp_types.Result):
    """Result of `events/poll`."""

    events: list[EventOccurrence]
    cursor: str | None = None
    truncated: bool = False
    has_more: bool = False
    next_poll_ms: int = Field(ge=0)
    result_type: Literal["complete"] = "complete"


class ListEventsRequest(mcp_types.Request[ListEventsParams, Literal["events/list"]]):
    """Request for `events/list`."""

    method: Literal["events/list"] = "events/list"
    params: ListEventsParams


class PollEventsRequest(mcp_types.Request[PollEventsParams, Literal["events/poll"]]):
    """Request for `events/poll`."""

    method: Literal["events/poll"] = "events/poll"
    params: PollEventsParams

    name_param = "name"


class StreamEventsParams(mcp_types.RequestParams):
    """Parameters for `events/stream`: one subscription per request."""

    name: str
    arguments: dict[str, Any] | None = None
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    heartbeat_ms: int | None = Field(default=None, ge=1)


class StreamEventsResult(mcp_types.Result):
    """The empty result that closes an `events/stream` request."""

    result_type: Literal["complete"] = "complete"


class StreamEventsRequest(
    mcp_types.Request[StreamEventsParams, Literal["events/stream"]]
):
    """Request for `events/stream`."""

    method: Literal["events/stream"] = "events/stream"
    params: StreamEventsParams

    name_param = "name"


class StreamFrame(BaseModel):
    """Params of any `notifications/events/*` message on a push stream.

    `_meta` carries the stream's subscription ID. The other fields depend on
    the notification: `active` and `heartbeat` carry a cursor, `event` carries
    an occurrence and the cursor after it, `error` and `terminated` an error.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    meta: dict[str, Any] = Field(alias="_meta")
    cursor: str | None = None
    truncated: bool | None = None
    heartbeat_ms: int | None = Field(default=None, alias="heartbeatMs")
    error: dict[str, Any] | None = None


class StreamNotification(BaseModel):
    """A `notifications/events/*` message."""

    method: str
    params: StreamFrame


class WebhookTarget(BaseModel):
    """Where and how a webhook subscription delivers: `delivery` in `events/subscribe`."""

    mode: Literal["webhook"] = "webhook"
    url: str
    secret: str


class WebhookAddress(BaseModel):
    """The callback URL that names a subscription in `events/unsubscribe`."""

    url: str


class SubscribeEventsParams(mcp_types.RequestParams):
    """Parameters for `events/subscribe`: create or refresh one webhook subscription."""

    name: str
    arguments: dict[str, Any] | None = None
    delivery: WebhookTarget
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    ttl_ms: int | None = Field(default=None, ge=0)


class DeliveryStatus(BaseModel):
    """How deliveries to a webhook subscription are going, on a refresh."""

    model_config = ConfigDict(populate_by_name=True)

    active: bool = True
    last_delivery_at: str | None = Field(default=None, alias="lastDeliveryAt")
    last_error: str | None = Field(default=None, alias="lastError")
    failed_since: str | None = Field(default=None, alias="failedSince")


class SubscribeEventsResult(mcp_types.Result):
    """Result of `events/subscribe`."""

    id: str
    refresh_before: str | None
    cursor: str | None = None
    truncated: bool = False
    delivery_status: DeliveryStatus | None = None
    result_type: Literal["complete"] = "complete"


class SubscribeEventsRequest(
    mcp_types.Request[SubscribeEventsParams, Literal["events/subscribe"]]
):
    """Request for `events/subscribe`."""

    method: Literal["events/subscribe"] = "events/subscribe"
    params: SubscribeEventsParams

    name_param = "name"


class UnsubscribeEventsParams(mcp_types.RequestParams):
    """Parameters for `events/unsubscribe`."""

    name: str
    arguments: dict[str, Any] | None = None
    delivery: WebhookAddress


class UnsubscribeEventsResult(mcp_types.Result):
    """The empty result of `events/unsubscribe`."""

    result_type: Literal["complete"] = "complete"


class UnsubscribeEventsRequest(
    mcp_types.Request[UnsubscribeEventsParams, Literal["events/unsubscribe"]]
):
    """Request for `events/unsubscribe`."""

    method: Literal["events/unsubscribe"] = "events/unsubscribe"
    params: UnsubscribeEventsParams

    name_param = "name"
