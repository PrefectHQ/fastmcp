"""An event log kept in Docket's Redis, shared across replicas and restarts.

Experimental: this API may change in any release without a deprecation period.

Requires `pydocket`, which `pip install "fastmcp[tasks]"` provides.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastmcp.experimental.events.extension import Event
from fastmcp.experimental.events.log import EventLog, LoggedEvent, LogRead

if TYPE_CHECKING:
    from docket import Docket

_STREAM_ID = re.compile(r"^\d+-\d+$")
_START = "0-0"


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _order(stream_id: str) -> tuple[int, int]:
    milliseconds, _, sequence = stream_id.partition("-")
    return int(milliseconds), int(sequence)


class DocketEventLog(EventLog):
    """Keeps the last `max_events` emitted events per type in a Redis stream.

    Every server pointed at the same `url` and `name` reads and writes the
    same log, so an event emitted on one replica reaches subscribers polling
    any of them, and a client's cursor stays valid across restarts. A cursor
    is the Redis stream ID of the last event the client has seen.

    Pass `docket` to reuse a Docket the application already opened, such as
    the one the tasks extension runs on; otherwise this log opens its own
    connection to `url` for the server's lifespan.

    ```python
    from fastmcp.experimental.events import EventsExtension
    from fastmcp.experimental.events.docket_log import DocketEventLog

    events = EventsExtension(log=DocketEventLog("redis://localhost:6379/0"))
    ```
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        name: str = "fastmcp-events",
        max_events: int = 10_000,
        docket: Docket | None = None,
    ) -> None:
        if (url is None) == (docket is None):
            raise ValueError("Pass exactly one of url or docket")
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        try:
            import docket as _docket  # noqa: F401
        except ImportError as error:
            raise ImportError(
                'DocketEventLog requires pydocket: pip install "fastmcp[tasks]"'
            ) from error
        self.url = url
        self.name = name
        self.max_events = max_events
        self._docket = docket

    @asynccontextmanager
    async def _open(self) -> AsyncGenerator[None]:
        from docket import Docket

        assert self.url is not None
        async with Docket(name=self.name, url=self.url) as docket:
            self._docket = docket
            try:
                yield
            finally:
                self._docket = None

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        if self.url is None:
            return super().lifespan()
        return self._open()

    @property
    def docket(self) -> Docket:
        if self._docket is None:
            raise RuntimeError(
                "DocketEventLog is not open; it opens with the server's lifespan"
            )
        return self._docket

    def _key(self, name: str) -> str:
        return self.docket.key(f"events:{name}")

    async def head(self, name: str) -> str:
        async with self.docket.redis() as redis:
            if not await redis.exists(self._key(name)):
                return _START
            info = await redis.xinfo_stream(self._key(name))
        return _text(info["last-generated-id"])

    async def append(self, name: str, entry: LoggedEvent) -> None:
        event = entry.event
        record = {
            "data": event.data,
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "meta": event.meta,
        }
        async with self.docket.redis() as redis:
            await redis.xadd(
                self._key(name),
                {"event": json.dumps(record)},
                maxlen=self.max_events,
            )

    async def _start(self, name: str, cursor: str) -> tuple[str, bool]:
        """Where to read after `cursor`, and whether events may have been skipped."""
        if not _STREAM_ID.match(cursor):
            # Not a stream ID: a cursor from another log. Replay what this
            # one holds, as a gap.
            return _START, True
        async with self.docket.redis() as redis:
            if not await redis.exists(self._key(name)):
                return _START, cursor != _START
            info = await redis.xinfo_stream(self._key(name))
        position = _order(cursor)
        last = _text(info["last-generated-id"])
        if position > _order(last):
            # Ahead of anything this log wrote: it was reset under the client.
            return last, True
        # Redis 7 counts every entry ever added; when that is more than it
        # holds, it has trimmed some. Older Redis does not say, and a full
        # stream may have been trimmed. Either way a cursor older than the
        # oldest kept event may have missed some, so report a possible gap.
        length = info.get("length", 0)
        added = info.get("entries-added")
        trimmed = added > length if added is not None else length >= self.max_events
        first = info.get("first-entry")
        skipped = trimmed and bool(first) and position < _order(_text(first[0]))
        return cursor, skipped

    async def read(self, name: str, cursor: str, *, count: int) -> LogRead:
        cursor, truncated = await self._start(name, cursor)
        async with self.docket.redis() as redis:
            response = await redis.xread({self._key(name): cursor}, count=count + 1)
        rows = response[0][1] if response else []
        entries = [
            (_text(stream_id), LoggedEvent(_event(fields)))
            for stream_id, fields in rows
        ]
        page = entries[:count]
        return LogRead(
            entries=page,
            start=cursor,
            cursor=page[-1][0] if page else cursor,
            truncated=truncated,
            more=len(entries) > count,
        )

    async def wait(self, name: str, cursor: str | None, timeout: float) -> bool:
        milliseconds = max(int(timeout * 1000), 1)
        async with self.docket.redis() as redis:
            response = await redis.xread(
                {self._key(name): cursor or "$"}, count=1, block=milliseconds
            )
        return bool(response)


def _event(fields: dict[Any, Any]) -> Event:
    raw = fields.get(b"event", fields.get("event"))
    record = json.loads(_text(raw))
    timestamp = record.get("timestamp")
    return Event(
        data=record["data"],
        event_id=record.get("event_id"),
        timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
        meta=record.get("meta"),
    )
