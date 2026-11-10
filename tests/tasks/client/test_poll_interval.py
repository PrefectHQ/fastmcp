"""Fallback poll cadence for client-side task waiting (SEP-2663).

The modern protocol has no task status notifications, so the client polls. The
backoff ramps from a fast floor, doubling up to a ceiling: the server-advertised
``pollIntervalMs`` when present (a statement about server load), else the client
``poll_interval`` setting. A quick task resolves in ~20ms; a long one settles to
the advertised cadence.
"""

from __future__ import annotations

import pytest
from fastmcp_tasks.client import MIN_POLL_INTERVAL, _next_poll_delay, _poll_ceiling
from fastmcp_tasks.settings import TasksClientSettings, client_settings
from pydantic import ValidationError


@pytest.mark.parametrize("value", [0, -0.5, -1])
def test_non_positive_poll_interval_setting_is_rejected(value: float):
    with pytest.raises(ValidationError):
        TasksClientSettings(poll_interval=value)


def test_positive_poll_interval_setting_is_accepted():
    settings = TasksClientSettings(poll_interval=0.25)
    assert settings.poll_interval == 0.25


@pytest.mark.parametrize("poll_interval_ms", [2000, 30_000])
def test_advertised_interval_caps_the_ramp(poll_interval_ms: int):
    """An advertised interval is the ceiling the ramp tops out at."""
    assert _poll_ceiling(poll_interval_ms) == poll_interval_ms / 1000


def test_large_advertised_interval_is_honored():
    day_ms = 24 * 60 * 60 * 1000
    assert _poll_ceiling(day_ms) == 24 * 60 * 60


@pytest.mark.parametrize("poll_interval_ms", [None, 0, -1, -5000])
def test_absent_or_hostile_interval_falls_back_to_setting(poll_interval_ms):
    """An absent, zero, or negative server value cannot spin the client: use the setting."""
    assert _poll_ceiling(poll_interval_ms) == client_settings.poll_interval


def test_ramp_doubles_from_floor_up_to_advertised_ceiling():
    """Even with an advertised interval, the poll ramps fast then caps at it."""
    ceiling_ms = 500  # 0.5s ceiling
    delays = []
    backoff = MIN_POLL_INTERVAL
    for _ in range(7):
        delay, backoff = _next_poll_delay(ceiling_ms, backoff)
        delays.append(delay)

    assert delays == [0.02, 0.04, 0.08, 0.16, 0.32, 0.5, 0.5]


def test_first_delay_is_the_floor():
    delay, backoff = _next_poll_delay(30_000, MIN_POLL_INTERVAL)
    assert delay == MIN_POLL_INTERVAL
    assert backoff == MIN_POLL_INTERVAL * 2
