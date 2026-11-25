"""Where emitted events are kept between `emit()` and the polls that read them.

Experimental: this API may change in any release without a deprecation period.

An `EventLog` is an ordered, bounded log per event type. `emit()` appends to
it, and poll and push read from a cursor in it. The default `InMemoryEventLog`
lives in one process; a shared store can keep the log instead, so cursors survive a restart and
every replica serves the same events.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field

import anyio

from fastmcp_events.extension import Event


@dataclass(frozen=True)
class LogRead:
    """One read from a log, in order.

    Each entry comes with the cursor just after it. `start` is the cursor just
    before the first entry, and `cursor` the one after the last. `truncated`
    means the log could not start where it was asked to (the cursor is older
    than what the log still holds, or from another log), so events may have
    been skipped. `more` means the read stopped at `count` with entries left.
    """

    entries: Sequence[tuple[str, Event]]
    start: str
    cursor: str
    truncated: bool = False
    more: bool = False


class EventLog:
    """An ordered, bounded log of emitted events, one per event type name.

    Subclass this to keep emitted events somewhere else. A cursor is an opaque
    string the log hands out; a client returns it on its next poll.
    """

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        """Entered with the server's lifespan, to open a connection if needed."""
        return nullcontext()

    async def head(self, name: str) -> str:
        """The cursor after everything logged for `name` so far."""
        raise NotImplementedError

    async def append(self, name: str, event: Event) -> None:
        """Add an event to the end of `name`'s log."""
        raise NotImplementedError

    async def read(self, name: str, cursor: str, *, count: int) -> LogRead:
        """Up to `count` events logged for `name` after `cursor`."""
        raise NotImplementedError

    async def wait(self, name: str, cursor: str | None, timeout: float) -> bool:
        """Wait up to `timeout` seconds for an event after `cursor`.

        Returns whether one arrived. A `None` cursor waits for the next one.
        """
        raise NotImplementedError


@dataclass
class _Buffer:
    epoch: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    entries: deque[tuple[int, Event]] = field(default_factory=deque)
    next: int = 0
    changed: anyio.Event = field(default_factory=anyio.Event)


class InMemoryEventLog(EventLog):
    """Keeps the last `max_events` events per type in this process.

    Cursors are scoped to the process: after a restart, or on another replica,
    a client's old cursor is answered from the start of the buffer with
    `truncated=True`.
    """

    def __init__(self, max_events: int = 1_000) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self.max_events = max_events
        self._buffers: dict[str, _Buffer] = {}

    def _buffer(self, name: str) -> _Buffer:
        buffer = self._buffers.get(name)
        if buffer is None:
            buffer = self._buffers[name] = _Buffer(
                entries=deque(maxlen=self.max_events)
            )
        return buffer

    async def head(self, name: str) -> str:
        buffer = self._buffer(name)
        return f"{buffer.epoch}-{buffer.next}"

    async def append(self, name: str, event: Event) -> None:
        buffer = self._buffer(name)
        buffer.entries.append((buffer.next, event))
        buffer.next += 1
        changed, buffer.changed = buffer.changed, anyio.Event()
        changed.set()

    def _start(self, buffer: _Buffer, cursor: str) -> tuple[int, bool]:
        """The first position after `cursor`, and whether events were skipped."""
        oldest = buffer.entries[0][0] if buffer.entries else buffer.next
        epoch, _, raw = cursor.rpartition("-")
        if epoch != buffer.epoch or not raw.isdigit():
            # A cursor from another process (a restart, another replica) says
            # nothing about this buffer: replay what it has, as a gap.
            return oldest, True
        start = int(raw)
        if start > buffer.next:
            return buffer.next, True
        if start < oldest:
            return oldest, True
        return start, False

    async def read(self, name: str, cursor: str, *, count: int) -> LogRead:
        buffer = self._buffer(name)
        start, truncated = self._start(buffer, cursor)
        after = [(p, e) for p, e in buffer.entries if p >= start]
        page = after[:count]
        end = page[-1][0] + 1 if page else start
        return LogRead(
            entries=[(f"{buffer.epoch}-{p + 1}", e) for p, e in page],
            start=f"{buffer.epoch}-{start}",
            cursor=f"{buffer.epoch}-{end}",
            truncated=truncated,
            more=len(after) > count,
        )

    async def wait(self, name: str, cursor: str | None, timeout: float) -> bool:
        buffer = self._buffer(name)
        # Taken before the comparison, so an append that lands while this runs
        # still wakes the wait below.
        changed = buffer.changed
        if cursor is not None and cursor != await self.head(name):
            return True
        with anyio.move_on_after(timeout):
            await changed.wait()
        return changed.is_set()
