"""Fixtures with real PEP 563 (``from __future__ import annotations``).

Do not strip the future import: it is what makes return annotations stored as
strings, so ``get_type_hints`` resolves ``-> None`` to the ``NoneType`` class.
"""

from __future__ import annotations

from mcp_types import InputRequiredResult


def do_nothing() -> None:
    return None


async def do_nothing_async() -> None:
    return None


async def do_nothing_guarded_async() -> InputRequiredResult | None:
    return None


def give_int() -> int:
    return 42
