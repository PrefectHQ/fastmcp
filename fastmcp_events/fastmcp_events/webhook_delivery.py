"""Webhook delivery for the MCP Events extension (SEP-3415).

Experimental: this API may change in any release without a deprecation period.

A client calls `events/subscribe` with an `https` callback URL and a signing
secret, and keeps calling it before the granted lease runs out. The server
keeps each subscription in a key-value store under that lease, and a sender
reads each subscription's events from its cursor and POSTs them, signed, one
at a time in order. Subscriptions, cursors and delivery status all live in the
store, so any replica can answer a refresh and any replica can send.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import ssl
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

import anyio
import httpx2
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel

from fastmcp.utilities.logging import get_logger
from fastmcp_events._constants import (
    CALLBACK_ENDPOINT_ERROR,
    FORBIDDEN,
    NOT_FOUND,
    UNSUPPORTED,
)
from fastmcp_events.extension import EventPoll
from fastmcp_events.models import (
    DeliveryStatus,
    EventOccurrence,
    SubscribeEventsParams,
    SubscribeEventsResult,
)
from fastmcp_events.webhooks import secret_bytes, signed_headers

if TYPE_CHECKING:
    from key_value.aio.protocols import AsyncKeyValue

    from fastmcp_events.registration import EventRegistration

logger = get_logger(__name__)

_COLLECTION = "fastmcp-events-webhooks"
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_TERMINAL_CODES = frozenset({NOT_FOUND, FORBIDDEN, UNSUPPORTED})
# One pass delivers at most this many events per subscription, so one busy
# subscription cannot hold a pass open forever.
_EVENTS_PER_PASS = 100
_MAX_BACKOFF = timedelta(minutes=2)


class _Subscription(BaseModel):
    """What the client asked for: written only by `events/subscribe`."""

    id: str
    principal: str
    url: str
    name: str
    arguments: dict[str, Any]
    secret: str
    refresh_before: datetime


class _Progress(BaseModel):
    """How far delivery got: written by the sender."""

    cursor: str | None = None
    max_age_ms: int | None = None
    last_delivery_at: datetime | None = None
    last_error: str | None = None
    failed_since: datetime | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None


class _Failure(Exception):
    """A delivery attempt failed, in one of the categories SEP-3415 names."""

    def __init__(self, category: str, *, retry: bool = True) -> None:
        super().__init__(category)
        self.category = category
        self.retry = retry


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment else None


def _digest(*parts: Any) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class WebhookDelivery:
    """Offer webhook delivery: `events/subscribe` and signed POSTs.

    Experimental: this API may change in any release without a deprecation
    period.

    Pass one to `EventsExtension(webhooks=...)`. Subscriptions require an
    authenticated client, since a subscription is keyed by who asked.

    Replicas of one server share a `store`. Where no process lives long
    enough to run the sender, set `sender=False` and call
    `EventsExtension.deliver_webhooks()` on a schedule.

    Args:
        store: Where subscriptions and their progress are kept. Defaults to
            memory, which only one process can see.
        max_ttl: The longest lease granted. Clients refresh before it ends.
        min_ttl: The shortest lease granted; shorter requests are raised to it.
        interval: Seconds between sender passes. An emit wakes the sender in
            its own process at once.
        retry_window: How long one event is retried before it is given up.
        sender: Whether this process runs the sender with the server.
        allow_private_addresses: Deliver to loopback and private addresses.
            Off by default, so a client cannot point the server at its own
            network.
        ssl_context: TLS settings for deliveries, for example to trust a
            private certificate authority.
        timeout: Seconds to wait for a receiver to answer one POST.
    """

    def __init__(
        self,
        *,
        store: AsyncKeyValue | None = None,
        max_ttl: timedelta = timedelta(hours=1),
        min_ttl: timedelta = timedelta(minutes=1),
        interval: float = 5.0,
        retry_window: timedelta = timedelta(minutes=10),
        sender: bool = True,
        allow_private_addresses: bool = False,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = 5.0,
    ) -> None:
        if min_ttl > max_ttl:
            raise ValueError("min_ttl must not exceed max_ttl")
        if store is None:
            from key_value.aio.stores.memory import MemoryStore

            store = MemoryStore()
        self.store = store
        self.max_ttl = max_ttl
        self.min_ttl = min_ttl
        self.interval = interval
        self.retry_window = retry_window
        self.sender = sender
        self.allow_private_addresses = allow_private_addresses
        self.ssl_context = ssl_context
        self.timeout = timeout
        self._wake = anyio.Event()
        self._sender_id = secrets.token_hex(8)

    # Storage ---------------------------------------------------------------

    async def _get(self, key: str) -> dict[str, Any] | None:
        return await self.store.get(key, collection=_COLLECTION)

    async def _put(self, key: str, value: BaseModel | Mapping[str, Any], ttl: float):
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        await self.store.put(key, dict(data), collection=_COLLECTION, ttl=ttl)

    async def _delete(self, key: str) -> None:
        await self.store.delete(key, collection=_COLLECTION)

    async def _load(self, model: type[_ModelT], key: str) -> _ModelT | None:
        record = await self._get(key)
        return model.model_validate(record) if record else None

    async def _save_progress(self, sub: _Subscription, progress: _Progress) -> None:
        # Kept for the longest lease rather than this one: a refresh can extend
        # the lease while a sender holding the old one is still saving.
        # Progress that outlives its subscription is never read.
        await self._put(f"progress-{sub.id}", progress, self.max_ttl.total_seconds())

    async def _index(self, name: str) -> dict[str, float]:
        """The live subscription IDs for an event type, with when each lapses.

        The store has no way to list keys, so each event type keeps an index.
        Two replicas adding to it at once can lose an entry; every refresh
        adds its subscription back, so a lost entry returns within a lease,
        and its saved progress means the events it missed are sent then.
        """
        record = await self._get(f"index-{name}") or {}
        now = _now().timestamp()
        return {key: until for key, until in record.items() if until > now}

    async def _set_index(self, name: str, entries: dict[str, float]) -> None:
        await self._put(f"index-{name}", entries, self.max_ttl.total_seconds())

    # Requests --------------------------------------------------------------

    def _check_target(self, url: str, secret: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise MCPError(INVALID_PARAMS, "delivery.url must be an https URL")
        try:
            secret_bytes(secret)
        except ValueError as error:
            raise MCPError(INVALID_PARAMS, f"delivery.secret: {error}") from None

    def _grant(self, ttl_ms: int | None) -> timedelta:
        # A missing or null suggestion gets the longest lease; no expiry is
        # never granted, which SEP-3415 allows.
        if ttl_ms is None:
            return self.max_ttl
        return min(max(timedelta(milliseconds=ttl_ms), self.min_ttl), self.max_ttl)

    async def subscribe(
        self,
        registration: EventRegistration,
        params: SubscribeEventsParams,
        principal: str | None,
    ) -> SubscribeEventsResult:
        """Create or refresh one webhook subscription."""
        if principal is None:
            raise MCPError(
                FORBIDDEN, "Webhook delivery requires an authenticated client"
            )
        target = params.delivery
        self._check_target(target.url, target.secret)
        arguments = params.arguments or {}
        registration.arguments(arguments)
        subscription_id = (
            "sub_" + _digest(principal, target.url, registration.name, arguments)[:24]
        )
        existing = await self._load(_Subscription, f"sub-{subscription_id}")
        progress = (
            await self._load(_Progress, f"progress-{subscription_id}")
            if existing
            else None
        )
        truncated = False
        if progress is None:
            # New, or lost to a restart of a memory store: start where the
            # client says, which is how a lapsed subscription recovers.
            progress, truncated = await self._start(registration, arguments, params)
        await self._verify_endpoint(principal, subscription_id, target)

        now = _now()
        grant = self._grant(params.ttl_ms)
        sub = _Subscription(
            id=subscription_id,
            principal=principal,
            url=target.url,
            name=registration.name,
            arguments=arguments,
            secret=target.secret,
            refresh_before=now + grant,
        )
        await self._put(f"sub-{subscription_id}", sub, grant.total_seconds())
        await self._save_progress(sub, progress)
        index = await self._index(registration.name)
        index[subscription_id] = sub.refresh_before.timestamp()
        await self._set_index(registration.name, index)
        status = None
        if existing is not None:
            status = DeliveryStatus(
                last_delivery_at=_iso(progress.last_delivery_at),
                last_error=progress.last_error,
                failed_since=_iso(progress.failed_since),
            )
        return SubscribeEventsResult(
            id=subscription_id,
            refresh_before=_iso(sub.refresh_before),
            cursor=progress.cursor,
            truncated=truncated,
            delivery_status=status,
        )

    async def _start(
        self,
        registration: EventRegistration,
        arguments: dict[str, Any],
        params: SubscribeEventsParams,
    ) -> tuple[_Progress, bool]:
        """Where a new subscription starts delivering, and whether that skips events."""
        if params.cursor is None:
            batch = await registration.poll(EventPoll(arguments=arguments))
            if batch.cursor is None:
                # The sender resumes from a saved position on every pass, so
                # an event type with no replay has nothing to send from.
                raise MCPError(
                    UNSUPPORTED,
                    f"{registration.name!r} keeps no cursor, so it cannot be "
                    "delivered by webhook; poll or push it instead",
                )
            return _Progress(cursor=batch.cursor), False
        batch = await registration.poll(
            EventPoll(
                arguments=arguments,
                cursor=params.cursor,
                max_age_ms=params.max_age_ms,
                max_events=1,
            )
        )
        if batch.truncated and not batch.events:
            return _Progress(cursor=batch.cursor), True
        # Events are waiting after the cursor: the sender replays them.
        progress = _Progress(cursor=params.cursor, max_age_ms=params.max_age_ms)
        return progress, batch.truncated

    async def unsubscribe(
        self,
        registration: EventRegistration,
        url: str,
        arguments: dict[str, Any],
        principal: str | None,
    ) -> None:
        """Remove one webhook subscription now, without waiting for its lease."""
        if principal is None:
            raise MCPError(
                FORBIDDEN, "Webhook delivery requires an authenticated client"
            )
        subscription_id = (
            "sub_" + _digest(principal, url, registration.name, arguments)[:24]
        )
        if await self._load(_Subscription, f"sub-{subscription_id}") is None:
            raise MCPError(
                NOT_FOUND, "No such subscription", data={"kind": "subscription"}
            )
        await self._forget(registration.name, subscription_id)

    async def _forget(self, name: str, subscription_id: str) -> None:
        await self._delete(f"sub-{subscription_id}")
        await self._delete(f"progress-{subscription_id}")
        index = await self._index(name)
        if index.pop(subscription_id, None) is not None:
            await self._set_index(name, index)

    async def _verify_endpoint(
        self, principal: str, subscription_id: str, target: Any
    ) -> None:
        """Confirm the receiver wants deliveries before the first one.

        The receiver echoes a one-time challenge, signed like a delivery, so
        no one can point a subscription at a URL that did not agree to it.
        A confirmation covers one client's subscriptions to one URL for
        `max_ttl`, renewed by every subscribe.
        """
        key = "verified-" + _digest(principal, target.url)[:32]
        ttl = self.max_ttl.total_seconds()
        if await self._get(key) is not None:
            await self._put(key, {"verified": True}, ttl)
            return
        challenge = secrets.token_urlsafe(24)
        body = {"type": "verification", "challenge": challenge}
        message_id = f"msg_verification_{secrets.token_hex(12)}"
        async with self._client() as client:
            try:
                response = await self._post(
                    client, target.url, target.secret, message_id, subscription_id, body
                )
            except _Failure as failure:
                raise MCPError(
                    CALLBACK_ENDPOINT_ERROR,
                    "The callback endpoint could not be verified",
                    data={"reason": failure.category},
                ) from None
        echoed = None
        try:
            answer = response.json()
            if isinstance(answer, dict):
                echoed = answer.get("challenge")
        except ValueError:
            pass
        # Compared as bytes: `compare_digest` raises on a non-ASCII str.
        if not isinstance(echoed, str) or not hmac.compare_digest(
            echoed.encode(), challenge.encode()
        ):
            raise MCPError(
                CALLBACK_ENDPOINT_ERROR,
                "The callback endpoint did not echo the challenge",
                data={"reason": "challenge_failed"},
            )
        await self._put(key, {"verified": True}, ttl)

    # Sending ---------------------------------------------------------------

    def _client(self) -> httpx2.AsyncClient:
        # No proxies from the environment: the connection must go to the
        # address that was checked, and redirects are never followed.
        return httpx2.AsyncClient(
            verify=self.ssl_context or True,
            trust_env=False,
            follow_redirects=False,
            timeout=self.timeout,
        )

    async def _pinned_address(self, host: str, port: int) -> str:
        """Resolve `host` once and check where it points, against DNS rebinding."""
        try:
            infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise _Failure("connection_refused") from None
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not addresses:
            raise _Failure("connection_refused")
        if not self.allow_private_addresses and not all(
            address.is_global for address in addresses
        ):
            # Reported like an unreachable host, so the error says nothing
            # about the server's own network.
            raise _Failure("connection_refused")
        return str(addresses[0])

    async def _post(
        self,
        client: httpx2.AsyncClient,
        url: str,
        secret: str,
        message_id: str,
        subscription_id: str,
        payload: Mapping[str, Any],
    ) -> httpx2.Response:
        """POST one signed body to the address `url` resolves to now."""
        parts = urlsplit(url)
        host = parts.hostname
        assert host is not None
        port = parts.port or 443
        address = await self._pinned_address(host, port)
        literal = f"[{address}]" if ":" in address else address
        pinned = urlunsplit(parts._replace(netloc=f"{literal}:{port}"))
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = signed_headers(secret, message_id, subscription_id, body)
        headers["host"] = parts.netloc.rpartition("@")[2]
        try:
            response = await client.post(
                pinned,
                content=body,
                headers=headers,
                extensions={"sni_hostname": host},
            )
        except httpx2.TimeoutException:
            raise _Failure("timeout") from None
        except httpx2.ConnectError as error:
            category = (
                "tls_error"
                if isinstance(error.__cause__, ssl.SSLError)
                else ("connection_refused")
            )
            raise _Failure(category) from None
        except httpx2.HTTPError:
            raise _Failure("connection_refused") from None
        if response.is_success:
            return response
        # 410 and 413 say "do not send this again"; the event is given up.
        retry = response.status_code not in (410, 413)
        if 400 <= response.status_code < 500:
            raise _Failure("http_4xx", retry=retry)
        raise _Failure("http_5xx", retry=retry)

    def wake(self) -> None:
        """Start the next sender pass in this process now."""
        self._wake.set()

    async def run(
        self, registrations: Callable[[], Mapping[str, EventRegistration]]
    ) -> None:
        """Send until cancelled: one pass, then wait for an emit or the interval."""
        while True:
            self._wake = anyio.Event()
            try:
                await self.deliver(registrations())
            except Exception:
                logger.exception("Webhook delivery pass failed")
            with anyio.move_on_after(self.interval):
                await self._wake.wait()

    async def deliver(self, registrations: Mapping[str, EventRegistration]) -> None:
        """One pass over every live subscription, sending what each is owed."""
        limiter = anyio.CapacityLimiter(16)
        async with self._client() as client, anyio.create_task_group() as tg:
            for name, registration in registrations.items():
                for subscription_id in await self._index(name):

                    async def one(
                        registration: EventRegistration = registration,
                        subscription_id: str = subscription_id,
                    ) -> None:
                        async with limiter:
                            if not await self._claim(subscription_id):
                                return
                            try:
                                await self._deliver_one(
                                    client, registration, subscription_id
                                )
                            finally:
                                await self._release(subscription_id)

                    tg.start_soon(one)

    async def _claim(self, subscription_id: str) -> bool:
        """Take this subscription's sending turn unless another sender holds it.

        The store has no compare-and-swap, so two senders racing for the same
        turn can both win; their deliveries carry the same webhook-id and the
        receiver drops the copy. Reading the claim back after writing it makes
        that window small.
        """
        held = await self._get(f"sending-{subscription_id}")
        if held and held.get("by") != self._sender_id:
            return False
        await self._hold(subscription_id)
        held = await self._get(f"sending-{subscription_id}")
        return held is not None and held.get("by") == self._sender_id

    async def _hold(self, subscription_id: str) -> None:
        # Outlives one POST and its retry wait, so a sender that dies mid-pass
        # hands the subscription on within seconds.
        ttl = 3 * self.timeout + self.interval
        await self._put(f"sending-{subscription_id}", {"by": self._sender_id}, ttl)

    async def _release(self, subscription_id: str) -> None:
        held = await self._get(f"sending-{subscription_id}")
        if held and held.get("by") == self._sender_id:
            await self._delete(f"sending-{subscription_id}")

    async def _deliver_one(
        self,
        client: httpx2.AsyncClient,
        registration: EventRegistration,
        subscription_id: str,
    ) -> None:
        sub = await self._load(_Subscription, f"sub-{subscription_id}")
        if sub is None:
            await self._forget(registration.name, subscription_id)
            return
        progress = (
            await self._load(_Progress, f"progress-{subscription_id}") or _Progress()
        )
        if progress.next_attempt_at and _now() < progress.next_attempt_at:
            return
        sent = 0
        while sent < _EVENTS_PER_PASS:
            if sent:
                await self._hold(subscription_id)
                # Cancelled or lapsed mid-backlog: stop now, not next pass.
                current = await self._load(_Subscription, f"sub-{subscription_id}")
                if current is None:
                    return
                sub = current
            try:
                batch = await registration.poll(
                    EventPoll(
                        arguments=sub.arguments,
                        cursor=progress.cursor,
                        max_age_ms=progress.max_age_ms,
                        max_events=1,
                    )
                )
            except MCPError as error:
                if error.error.code in _TERMINAL_CODES:
                    await self._terminate(client, sub, error)
                    return
                logger.warning("Reading %r for a webhook failed: %s", sub.name, error)
                return
            except Exception:
                logger.exception("Reading %r for a webhook failed", sub.name)
                return
            if progress.cursor is None:
                # No position yet (its progress was lost): start from now.
                progress.cursor = batch.cursor
                await self._save_progress(sub, progress)
                return
            start = progress.cursor
            if batch.truncated:
                await self._send_control(
                    client,
                    sub,
                    "gap",
                    {"cursor": start if batch.events else batch.cursor},
                    at=start,
                )
            for position, event in enumerate(batch.events):
                last = position == len(batch.events) - 1
                occurrence = EventOccurrence(
                    event_id=event.event_id or _digest(sub.id, start, position),
                    name=sub.name,
                    timestamp=_iso(event.timestamp or _now()) or "",
                    data=event.data,
                    meta=event.meta,
                )
                payload = occurrence.model_dump(by_alias=True, exclude_none=True)
                payload["cursor"] = batch.cursor if last else start
                # The same message gets the same ID from every replica and
                # every retry, so receivers can drop repeats. It comes from
                # the subscription and the log position, never the eventId,
                # which the upstream chose and every subscription shares.
                message_id = "msg_" + _digest(sub.id, start, position)[:32]
                try:
                    await self._post(
                        client, sub.url, sub.secret, message_id, sub.id, payload
                    )
                except _Failure as failure:
                    if self._keep_retrying(progress, failure):
                        await self._save_progress(sub, progress)
                        return
                else:
                    progress.last_delivery_at = _now()
                    progress.last_error = None
                progress.failed_since = None
                progress.attempts = 0
                progress.next_attempt_at = None
                sent += 1
            progress.cursor = batch.cursor
            progress.max_age_ms = None
            await self._save_progress(sub, progress)
            if not batch.events and not batch.has_more:
                return

    def _keep_retrying(self, progress: _Progress, failure: _Failure) -> bool:
        """Record a failed attempt; whether the event is retried rather than given up."""
        now = _now()
        progress.last_error = failure.category
        if not failure.retry:
            return False
        progress.failed_since = progress.failed_since or now
        if now - progress.failed_since > self.retry_window:
            logger.warning("Giving up on a webhook delivery after %s", failure)
            return False
        progress.attempts += 1
        backoff = timedelta(seconds=self.interval * 2 ** (progress.attempts - 1))
        progress.next_attempt_at = now + min(backoff, _MAX_BACKOFF)
        return True

    async def _send_control(
        self,
        client: httpx2.AsyncClient,
        sub: _Subscription,
        kind: str,
        fields: Mapping[str, Any],
        at: str | None = None,
    ) -> None:
        """POST a `gap` or `terminated` envelope, once.

        Its ID comes from the subscription and the position it was sent at,
        so a gap resent on a later pass (its first event failed and is being
        retried) reaches the receiver with the same ID.
        """
        message_id = f"msg_{kind}_" + _digest(sub.id, at or "")[:24]
        try:
            await self._post(
                client,
                sub.url,
                sub.secret,
                message_id,
                sub.id,
                {"type": kind, **fields},
            )
        except _Failure as failure:
            logger.warning("Sending a %s envelope failed: %s", kind, failure)

    async def _terminate(
        self, client: httpx2.AsyncClient, sub: _Subscription, error: MCPError
    ) -> None:
        await self._send_control(
            client,
            sub,
            "terminated",
            {"error": error.error.model_dump(exclude_none=True)},
        )
        await self._forget(sub.name, sub.id)
