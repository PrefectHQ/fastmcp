"""Pagination utilities for MCP list operations."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass
class CursorState:
    """Internal representation of pagination cursor state.

    The cursor encodes the offset into the result set. This is opaque to clients
    per the MCP spec - they should not parse or modify cursors.
    """

    offset: int

    def encode(self) -> str:
        """Encode cursor state to an opaque string."""
        data = json.dumps({"o": self.offset})
        return base64.urlsafe_b64encode(data.encode()).decode()

    @classmethod
    def decode(cls, cursor: str) -> CursorState:
        """Decode cursor from an opaque string.

        Raises:
            ValueError: If the cursor is invalid or malformed.
        """
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            offset = data["o"]
            # The offset reaches a slice and an addition, so a string or a float
            # surfaces as a TypeError the caller reports as an internal error, and
            # a negative one slices from the end and returns a valid-looking page
            # the client never asked for. A cursor is ours to produce, so anything
            # but a whole non-negative count means it was tampered with.
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError(
                    f"cursor offset must be a non-negative integer: {offset!r}"
                )
            return cls(offset=offset)
        except (
            json.JSONDecodeError,
            KeyError,
            ValueError,
            TypeError,
            binascii.Error,
        ) as e:
            raise ValueError(f"Invalid cursor: {cursor}") from e


def paginate_sequence(
    items: Sequence[T],
    cursor: str | None,
    page_size: int,
) -> tuple[list[T], str | None]:
    """Paginate a sequence of items.

    Args:
        items: The full sequence to paginate.
        cursor: Optional cursor from a previous request. None for first page.
        page_size: Maximum number of items per page.

    Returns:
        Tuple of (page_items, next_cursor). next_cursor is None if no more pages.

    Raises:
        ValueError: If the cursor is invalid.
    """
    offset = 0
    if cursor:
        state = CursorState.decode(cursor)
        offset = state.offset

    end = offset + page_size
    page = list(items[offset:end])

    next_cursor = None
    if end < len(items):
        next_cursor = CursorState(offset=end).encode()

    return page, next_cursor
