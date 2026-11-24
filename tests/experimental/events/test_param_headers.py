"""Subscription arguments annotated with `x-mcp-header` travel as `Mcp-Param-*`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import HEADER_MISMATCH
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

from fastmcp import Client, FastMCP
from fastmcp.experimental.events import (
    Event,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
    list_events,
    poll_events_mcp,
    subscribe_events,
)
from fastmcp.experimental.events import subscription as subscription_module
from fastmcp.experimental.events._constants import EVENTS_EXTENSION_ID
from fastmcp.server.dependencies import get_http_headers
from fastmcp.utilities.tests import run_server_async

Region = Annotated[str, Field(json_schema_extra={"x-mcp-header": "Region"})]


class IncidentCreated(BaseModel):
    """An incident was opened."""

    region: str

    def for_subscription(
        self, region: Region = "", severity: str | None = None
    ) -> IncidentCreated:
        return self


def make_server(seen_headers: list[str | None]) -> tuple[FastMCP, EventsExtension]:
    server = FastMCP("incidents")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)
    events.event("incident.created")(IncidentCreated)

    @events.reader(IncidentCreated)
    def read_incidents(poll: EventPoll) -> EventBatch:
        seen_headers.append(get_http_headers(include_all=True).get("mcp-param-region"))
        if poll.cursor is None:
            return EventBatch(cursor="0")
        region = poll.arguments["region"]
        return EventBatch(
            events=[Event(data={"region": region}, event_id=f"incident-{region}")],
            cursor="1",
        )

    return server, events


async def test_poll_mirrors_annotated_arguments_into_headers() -> None:
    seen: list[str | None] = []
    server, _ = make_server(seen)

    async with run_server_async(server) as url:
        async with Client(url, extensions=[EventsClientExtension()]) as client:
            await list_events(client)
            result = await poll_events_mcp(
                client, "incident.created", {"region": "us-west1"}, cursor="0"
            )

    assert [event.data for event in result.events] == [{"region": "us-west1"}]
    assert seen == ["us-west1"]


async def test_push_mirrors_annotated_arguments_into_headers() -> None:
    seen: list[str | None] = []
    server, _ = make_server(seen)

    async with run_server_async(server) as url:
        async with Client(url, extensions=[EventsClientExtension()]) as client:
            subscription = subscribe_events(
                client, "incident.created", {"region": "eu-west1"}, cursor="0"
            )
            with anyio.fail_after(15):
                async for event in subscription:
                    assert event.data == {"region": "eu-west1"}
                    break

    assert seen[0] == "eu-west1"


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_a_subscription_with_a_chosen_delivery_sends_param_headers(
    delivery: Literal["poll", "push"],
) -> None:
    seen: list[str | None] = []
    server, _ = make_server(seen)

    async with run_server_async(server) as url:
        async with Client(url, extensions=[EventsClientExtension()]) as client:
            subscription = subscribe_events(
                client,
                "incident.created",
                {"region": "ap-south1"},
                cursor="0",
                delivery=delivery,
            )
            with anyio.fail_after(15):
                async for event in subscription:
                    assert event.data == {"region": "ap-south1"}
                    break

    assert seen[0] == "ap-south1"


def post_poll(http: TestClient, arguments: dict, headers: dict[str, str]):
    return http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "events/poll",
            "params": {
                "name": "incident.created",
                "arguments": arguments,
                "cursor": "0",
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {
                        "extensions": {EVENTS_EXTENSION_ID: {}}
                    },
                },
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "events/poll",
            "Mcp-Name": "incident.created",
            **headers,
        },
    )


@pytest.mark.parametrize(
    "headers",
    [{}, {"Mcp-Param-Region": "us-east1"}],
    ids=["missing", "mismatched"],
)
def test_server_rejects_a_param_header_that_disagrees_with_the_body(
    headers: dict[str, str],
) -> None:
    server, _ = make_server([])
    app = server.http_app(stateless_http=True, json_response=True)

    with TestClient(app) as http:
        response = post_poll(http, {"region": "us-west1"}, headers)

    assert response.json()["error"]["code"] == HEADER_MISMATCH


def test_server_accepts_a_matching_param_header() -> None:
    server, _ = make_server([])
    app = server.http_app(stateless_http=True, json_response=True)

    with TestClient(app) as http:
        response = post_poll(
            http, {"region": "us-west1"}, {"Mcp-Param-Region": "us-west1"}
        )

    assert response.json()["result"]["events"][0]["data"] == {"region": "us-west1"}


class Bad(BaseModel):
    """Routed on an object, which no header can carry."""

    def for_subscription(
        self,
        r: Annotated[dict[str, Any], Field(json_schema_extra={"x-mcp-header": "R"})],
    ) -> Bad:
        return self


def test_registering_an_invalid_annotation_is_an_error() -> None:
    events = EventsExtension()
    with pytest.raises(ValueError, match="x-mcp-header"):
        events.event("bad")(Bad)


async def test_client_drops_an_event_type_with_an_invalid_annotation() -> None:
    server, events = make_server([])
    # A server SDK refuses to publish this, so plant it to stand in for one
    # that does not.
    events._registrations["incident.created"].input_schema = {
        "type": "object",
        "properties": {"r": {"type": "string", "x-mcp-header": "bad token"}},
    }

    async with Client(server, extensions=[EventsClientExtension()]) as client:
        assert await list_events(client) == []


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_a_header_mismatch_lists_again_and_retries(
    delivery: Literal["poll", "push"],
) -> None:
    seen: list[str | None] = []
    server, _ = make_server(seen)

    async with run_server_async(server) as url:
        extension = EventsClientExtension()
        async with Client(url, extensions=[extension]) as client:
            # Listed before the server annotated `region`: no headers known.
            extension._header_maps["incident.created"] = {}
            subscription = subscribe_events(
                client,
                "incident.created",
                {"region": "us-east1"},
                cursor="0",
                delivery=delivery,
            )
            with anyio.fail_after(15):
                async for event in subscription:
                    assert event.data == {"region": "us-east1"}
                    break

    assert extension._header_maps["incident.created"] == {("region",): "Region"}
    assert "us-east1" in seen


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_a_header_mismatch_is_retried_only_once(
    delivery: Literal["poll", "push"], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _ = make_server([])
    listings = 0

    async def stale_listing(client: Client) -> list:
        nonlocal listings
        listings += 1
        return []

    monkeypatch.setattr(subscription_module, "list_events", stale_listing)

    async with run_server_async(server) as url:
        extension = EventsClientExtension()
        async with Client(url, extensions=[extension]) as client:
            extension._header_maps["incident.created"] = {}
            subscription = subscribe_events(
                client,
                "incident.created",
                {"region": "us-east1"},
                cursor="0",
                delivery=delivery,
            )
            with pytest.raises(MCPError) as exc_info, anyio.fail_after(15):
                async for _ in subscription:
                    pass

    assert exc_info.value.error.code == HEADER_MISMATCH
    assert listings == 1
