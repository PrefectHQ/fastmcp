"""Timeout normalization utilities."""

from __future__ import annotations

import datetime


def normalize_timeout_to_timedelta(
    value: int | float | datetime.timedelta | None,
) -> datetime.timedelta | None:
    """Normalize a timeout value to a timedelta.

    Args:
        value: Timeout value as int/float (seconds), timedelta, or None

    Returns:
        timedelta if value provided, None otherwise
    """
    if value is None:
        return None
    if isinstance(value, datetime.timedelta):
        return value
    if isinstance(value, int | float):
        return datetime.timedelta(seconds=float(value))
    raise TypeError(f"Invalid timeout type: {type(value)}")


def normalize_timeout_to_seconds(
    value: int | float | datetime.timedelta | None,
) -> float | None:
    """Normalize a timeout value to seconds (float).

    Args:
        value: Timeout value as int/float (seconds), timedelta, or None.
            Zero values are treated as "disabled" and return None.

    Returns:
        float seconds if value provided and non-zero, None otherwise
    """
    if value is None:
        return None
    if isinstance(value, datetime.timedelta):
        seconds = value.total_seconds()
        return None if seconds == 0 else seconds
    if isinstance(value, int | float):
        return None if value == 0 else float(value)
    raise TypeError(f"Invalid timeout type: {type(value)}")
