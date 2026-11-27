from __future__ import annotations

import time
from datetime import timedelta

import anyio
import httpx2
import pytest
from fastmcp_events._constants import (
    CALLBACK_ENDPOINT_ERROR,
    FORBIDDEN,
    NOT_FOUND,
    UNSUPPORTED,
)
from fastmcp_events.webhook_delivery import _Progress, _Subscription
from fastmcp_events.webhooks import secret_bytes, sign
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.tests import asgi_client
from fastmcp_events import (
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
    WebhookDelivery,
    WebhookVerificationError,
    list_events,
    new_webhook_secret,
    subscribe_webhook,
    unsubscribe_webhook,
    verify_webhook,
)

HOOK = "https://localhost/hooks/a"


class AlertFired(BaseModel):
    """An alert fired"""

    key: str
    severity: str

    def for_subscription(self, severity: str | None = None) -> AlertFired | None:
        return self if severity is None or self.severity == severity else None


class Receiver:
    """A webhook endpoint behind `httpx2.MockTransport` that checks every POST."""

    def __init__(self) -> None:
        self.secrets: list[str] = []
        self.messages: list[tuple[httpx2.Request, dict]] = []
        self.statuses: list[int] = []
        self.echo = True

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        message = verify_webhook(self.secrets, request.headers, request.content)
        self.messages.append((request, message))
        if message.get("type") == "verification":
            challenge = message["challenge"] if self.echo else "nope"
            return httpx2.Response(200, json={"challenge": challenge})
        return httpx2.Response(self.statuses.pop(0) if self.statuses else 204)

    @property
    def events(self) -> list[dict]:
        return [m for _, m in self.messages if "type" not in m]


class MockedDelivery(WebhookDelivery):
    def __init__(self, receiver: Receiver, **kwargs) -> None:
        kwargs.setdefault("allow_private_addresses", True)
        super().__init__(sender=False, **kwargs)
        self.receiver = receiver

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.receiver))


def tokens() -> StaticTokenVerifier:
    return StaticTokenVerifier(
        tokens={
            "alice": {"client_id": "alice", "scopes": [], "sub": "alice"},
            "bob": {"client_id": "bob", "scopes": [], "sub": "bob"},
        }
    )


def make_server(webhooks: WebhookDelivery | None) -> tuple[FastMCP, EventsExtension]:
    server = FastMCP("alerts", auth=tokens())
    events = EventsExtension(webhooks=webhooks)
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
    return server, events


@pytest.fixture
def receiver() -> Receiver:
    receiver = Receiver()
    receiver.secrets.append(new_webhook_secret())
    return receiver


def connect(server: FastMCP, token: str = "alice"):
    return asgi_client(server, auth=token, extensions=[EventsClientExtension()])


class TestSigning:
    def test_round_trip(self):
        secret = new_webhook_secret()
        body = b'{"eventId":"e1"}'
        stamp = int(time.time())
        headers = {
            "webhook-id": "msg_1",
            "webhook-timestamp": str(stamp),
            "webhook-signature": sign(secret, "msg_1", stamp, body),
        }
        assert verify_webhook(secret, headers, body) == {"eventId": "e1"}
        # Any of several signatures verifies, as during secret rotation.
        rotated = {
            **headers,
            "webhook-signature": "v1,AAAA " + headers["webhook-signature"],
        }
        assert verify_webhook([new_webhook_secret(), secret], rotated, body)

    @pytest.mark.parametrize(
        "change",
        [
            {"body": b'{"eventId":"e2"}'},
            {"webhook-id": "msg_2"},
            {"webhook-timestamp": str(int(time.time()) - 600)},
        ],
    )
    def test_rejects_tampered_or_stale(self, change):
        secret = new_webhook_secret()
        stamp = int(time.time())
        body = b'{"eventId":"e1"}'
        headers = {
            "webhook-id": "msg_1",
            "webhook-timestamp": str(stamp),
            "webhook-signature": sign(secret, "msg_1", stamp, body),
        }
        body = change.pop("body", body)
        with pytest.raises(WebhookVerificationError):
            verify_webhook(secret, {**headers, **change}, body)

    @pytest.mark.parametrize(
        "secret", ["abc", "whsec_!!", "whsec_" + "QUJD" * 2, "whsec_" + "QUJD" * 40]
    )
    def test_secret_must_be_whsec_of_24_to_64_bytes(self, secret):
        with pytest.raises(ValueError):
            secret_bytes(secret)


async def test_listing_offers_webhook_only_when_configured(receiver):
    for webhooks, expected in [
        (None, ["poll", "push"]),
        (MockedDelivery(receiver), ["poll", "push", "webhook"]),
    ]:
        server, _ = make_server(webhooks)
        async with connect(server) as client:
            [event_type] = await list_events(client)
            assert event_type.delivery == expected


async def test_subscribe_requires_an_authenticated_client(receiver):
    server = FastMCP("alerts")
    events = EventsExtension(webhooks=MockedDelivery(receiver))
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
    async with Client(server, extensions=[EventsClientExtension()]) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
            )
    assert caught.value.error.code == FORBIDDEN


@pytest.mark.parametrize(
    "url, secret",
    [("http://localhost/hooks", None), (HOOK, "whsec_c2hvcnQ=")],
    ids=["not-https", "short-secret"],
)
async def test_subscribe_rejects_bad_targets(receiver, url, secret):
    server, _ = make_server(MockedDelivery(receiver))
    async with connect(server) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=url, secret=secret or receiver.secrets[0]
            )
    assert caught.value.error.code == INVALID_PARAMS
    assert receiver.messages == []


async def test_private_addresses_are_refused_by_default(receiver):
    server, _ = make_server(MockedDelivery(receiver, allow_private_addresses=False))
    async with connect(server) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
            )
    assert caught.value.error.code == CALLBACK_ENDPOINT_ERROR
    assert caught.value.error.data == {"reason": "connection_refused"}
    assert receiver.messages == []


async def test_endpoint_must_echo_the_challenge(receiver):
    receiver.echo = False
    server, _ = make_server(MockedDelivery(receiver))
    async with connect(server) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
            )
    assert caught.value.error.data == {"reason": "challenge_failed"}


async def test_delivers_signed_events_to_the_pinned_address(receiver):
    server, events = make_server(MockedDelivery(receiver))
    async with connect(server) as client:
        sub = await subscribe_webhook(
            client,
            "alert.fired",
            {"severity": "P1"},
            url=HOOK,
            secret=receiver.secrets[0],
        )
        [(challenge, verification)] = receiver.messages
        assert verification["type"] == "verification"
        assert challenge.headers["x-mcp-subscription-id"] == sub.id

        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.emit(AlertFired(key="b", severity="P3"), event_id="b")
        await events.emit(AlertFired(key="c", severity="P1"), event_id="c")
        await events.deliver_webhooks()

        assert [e["eventId"] for e in receiver.events] == ["a", "c"]
        request = receiver.messages[1][0]
        assert request.url.host in ("127.0.0.1", "::1")
        assert request.headers["host"] == "localhost"
        assert request.extensions["sni_hostname"] == "localhost"
        assert request.headers["x-mcp-subscription-id"] == sub.id

        refreshed = await subscribe_webhook(
            client,
            "alert.fired",
            {"severity": "P1"},
            url=HOOK,
            secret=receiver.secrets[0],
        )
    assert refreshed.id == sub.id
    assert refreshed.cursor == receiver.events[-1]["cursor"]
    assert refreshed.delivery_status is not None
    assert refreshed.delivery_status.last_error is None
    # A verified (principal, url) is not challenged again on refresh.
    assert sum("type" in m for _, m in receiver.messages) == 1


async def test_failed_delivery_is_retried_with_the_same_webhook_id(receiver):
    delivery = MockedDelivery(receiver, interval=0)
    server, events = make_server(delivery)
    async with connect(server) as client:
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        receiver.statuses = [503]
        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.deliver_webhooks()
        assert receiver.events == [receiver.messages[-1][1]]
        refreshed = await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        assert refreshed.delivery_status is not None
        assert refreshed.delivery_status.last_error == "http_5xx"
        await events.deliver_webhooks()
    first, second = (r for r, m in receiver.messages if "type" not in m)
    assert first.headers["webhook-id"] == second.headers["webhook-id"]


async def test_a_resent_gap_keeps_its_webhook_id(receiver):
    server = FastMCP("alerts", auth=tokens())
    events = EventsExtension(
        emit_buffer=1, webhooks=MockedDelivery(receiver, interval=0)
    )
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
    async with connect(server) as client:
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.emit(AlertFired(key="b", severity="P1"), event_id="b")
        # The gap goes through; the event after it fails and is retried.
        receiver.statuses = [204, 503]
        await events.deliver_webhooks()
        await events.deliver_webhooks()
    gaps = [r for r, m in receiver.messages if m.get("type") == "gap"]
    assert len(gaps) == 2
    assert gaps[0].headers["webhook-id"] == gaps[1].headers["webhook-id"]
    assert [e["eventId"] for e in receiver.events] == ["b", "b"]


async def test_gone_gives_the_event_up_and_moves_on(receiver):
    server, events = make_server(MockedDelivery(receiver))
    async with connect(server) as client:
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        receiver.statuses = [410]
        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.emit(AlertFired(key="b", severity="P1"), event_id="b")
        await events.deliver_webhooks()
    assert [e["eventId"] for e in receiver.events] == ["a", "b"]


async def test_unsubscribe_is_scoped_to_the_principal(receiver):
    server, events = make_server(MockedDelivery(receiver))
    async with connect(server) as alice, connect(server, "bob") as bob:
        await subscribe_webhook(
            alice, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        with pytest.raises(MCPError) as caught:
            await unsubscribe_webhook(bob, "alert.fired", url=HOOK)
        assert caught.value.error.code == NOT_FOUND
        await unsubscribe_webhook(alice, "alert.fired", url=HOOK)
        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.deliver_webhooks()
    assert receiver.events == []


async def test_lapsed_subscription_resumes_from_its_cursor(receiver):
    delivery = MockedDelivery(receiver, min_ttl=timedelta(milliseconds=200))
    server, events = make_server(delivery)
    async with connect(server) as client:
        sub = await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0], ttl_ms=1
        )
        await anyio.sleep(0.3)
        await events.emit(AlertFired(key="a", severity="P1"), event_id="a")
        await events.deliver_webhooks()
        assert receiver.events == []
        resumed = await subscribe_webhook(
            client,
            "alert.fired",
            url=HOOK,
            secret=receiver.secrets[0],
            cursor=sub.cursor,
        )
        assert resumed.id == sub.id and not resumed.truncated
        await events.deliver_webhooks()
    assert [e["eventId"] for e in receiver.events] == ["a"]


async def test_reader_that_forbids_terminates_the_subscription(receiver):
    server = FastMCP("alerts", auth=tokens())
    events = EventsExtension(webhooks=MockedDelivery(receiver))
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
    allowed = True

    @events.reader(AlertFired)
    def read(poll: EventPoll) -> EventBatch:
        if not allowed:
            raise MCPError(FORBIDDEN, "Access revoked")
        return EventBatch(cursor="0")

    async with connect(server) as client:
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        allowed = False
        await events.deliver_webhooks()
        [(_, terminated)] = [
            (r, m) for r, m in receiver.messages if m.get("type") == "terminated"
        ]
        assert terminated["error"]["code"] == FORBIDDEN
        with pytest.raises(MCPError) as caught:
            await unsubscribe_webhook(client, "alert.fired", url=HOOK)
    assert caught.value.error.code == NOT_FOUND


async def test_event_type_without_a_cursor_is_refused(receiver):
    server = FastMCP("alerts", auth=tokens())
    events = EventsExtension(webhooks=MockedDelivery(receiver))
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)

    @events.reader(AlertFired)
    def read(poll: EventPoll) -> EventBatch:
        return EventBatch()

    async with connect(server) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
            )
    assert caught.value.error.code == UNSUPPORTED
    # Refused before the endpoint is challenged.
    assert receiver.messages == []


async def test_unsubscribe_stops_a_backlog_mid_pass(receiver):
    delivery = MockedDelivery(receiver)
    server, events = make_server(delivery)
    async with connect(server) as client:
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
        )
        for key in "abc":
            await events.emit(AlertFired(key=key, severity="P1"), event_id=key)
        post = delivery._post

        async def post_then_cancel(*args, **kwargs):
            await post(*args, **kwargs)
            delivery._post = post
            await unsubscribe_webhook(client, "alert.fired", url=HOOK)

        delivery._post = post_then_cancel
        await events.deliver_webhooks()
    assert [e["eventId"] for e in receiver.events] == ["a"]


async def test_progress_outlives_a_stale_lease_save(receiver):
    delivery = MockedDelivery(receiver, min_ttl=timedelta(milliseconds=200))
    server, events = make_server(delivery)
    async with connect(server) as client:
        sub = await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0], ttl_ms=1
        )
        stale = await delivery._load(_Subscription, f"sub-{sub.id}")
        progress = await delivery._load(_Progress, f"progress-{sub.id}")
        assert stale is not None and progress is not None
        # A refresh extends the lease; then a sender holding the old lease saves.
        await subscribe_webhook(
            client, "alert.fired", url=HOOK, secret=receiver.secrets[0], ttl_ms=60_000
        )
        await delivery._save_progress(stale, progress)
        await anyio.sleep(0.3)
        assert await delivery._load(_Progress, f"progress-{sub.id}") is not None


class NonAsciiEcho(Receiver):
    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"challenge": "é"})


async def test_a_non_ascii_echo_fails_verification():
    receiver = NonAsciiEcho()
    receiver.secrets.append(new_webhook_secret())
    server, _ = make_server(MockedDelivery(receiver))
    async with connect(server) as client:
        with pytest.raises(MCPError) as caught:
            await subscribe_webhook(
                client, "alert.fired", url=HOOK, secret=receiver.secrets[0]
            )
    assert caught.value.error.code == CALLBACK_ENDPOINT_ERROR
