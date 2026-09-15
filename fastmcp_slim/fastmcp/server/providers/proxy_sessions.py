"""Request-scoped backend session ownership for proxy providers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from threading import Lock
from typing import Literal, cast
from weakref import ReferenceType, WeakKeyDictionary, ref

import anyio

from fastmcp.client.client import Client
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

ClientFactoryT = Callable[[], Client] | Callable[[], Awaitable[Client]]
ProxySessionScope = Literal["request", "operation"]


class RequestProxySession:
    """A lazily connected client retained for one frontend request."""

    def __init__(self, client_factory: ClientFactoryT) -> None:
        self._client_factory = client_factory
        self._client: Client | None = None
        self._retained: AsyncExitStack | None = None
        self._lock = anyio.Lock()

    async def get_client(self) -> Client:
        async with self._lock:
            if self._client is not None:
                return self._client

            client = self._client_factory()
            if inspect.isawaitable(client):
                client = cast(Client, await client)

            with _proxy_client_owners_lock:
                owner_ref = _proxy_client_owners.get(client)
                owner = owner_ref() if owner_ref is not None else None
                if client.is_connected() or (owner is not None and owner is not self):
                    client = client.new()
                _proxy_client_owners[client] = ref(self)
            self._client = client
            return client

    async def retain(self) -> None:
        async with self._lock:
            client = self._client
            if client is None:
                raise RuntimeError("Proxy request session has no client")
            if self._retained is not None and client.is_connected():
                return
            if self._retained is not None:
                await self._discard_retained()

            retained = AsyncExitStack()
            await retained.enter_async_context(client)
            self._retained = retained

    async def _discard_retained(self) -> None:
        retained, self._retained = self._retained, None
        if retained is None:
            return
        try:
            await retained.aclose()
        except Exception as error:
            logger.debug("Could not release dead proxy backend session: %r", error)

    async def close(self) -> None:
        async with self._lock:
            retained, self._retained = self._retained, None
            client = self._client
            try:
                if retained is not None:
                    await retained.aclose()
            finally:
                if client is not None:
                    with _proxy_client_owners_lock:
                        owner_ref = _proxy_client_owners.get(client)
                        if owner_ref is not None and owner_ref() is self:
                            del _proxy_client_owners[client]


_proxy_client_owners: WeakKeyDictionary[Client, ReferenceType[RequestProxySession]] = (
    WeakKeyDictionary()
)
_proxy_client_owners_lock = Lock()
