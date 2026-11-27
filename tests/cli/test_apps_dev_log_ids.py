"""Malformed JSON-RPC ids from the proxied upstream must not crash the dev
log: ``id`` may be any JSON value, and unhashable ones (lists, dicts) would
break the request-timing dict lookups. Mirrors the guard that
``_remember_request`` already had. Found while auditing the log module; in
the SSE path the per-line suppress only covers JSONDecodeError, so the
TypeError used to escape into the streaming response."""

import pytest

from fastmcp.cli.apps_dev_log import _MessageLog


@pytest.mark.anyio
async def test_unhashable_ids_do_not_crash_the_log() -> None:
    log = _MessageLog()
    log.log_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})

    for bad_id in ({"x": 1}, ["a"], 1.5, True, None):
        log.log_response({"jsonrpc": "2.0", "id": bad_id, "result": {}})

    # A well-formed response still matches the tracked request afterwards.
    log.log_response({"jsonrpc": "2.0", "id": 1, "result": {"late": True}})

    entries = [e for e in log.get_since(0) if e["direction"] == "response"]
    assert len(entries) == 6
    # None of the malformed ids crashed the log, and none stole the duration
    # (True would alias integer id 1 if the guard were missing).
    assert all(e["duration_ms"] is None for e in entries[:5])
    assert entries[5]["duration_ms"] is not None


def test_overlong_ids_are_not_tracked() -> None:
    log = _MessageLog()
    long_id = "x" * 200
    log.log_request({"jsonrpc": "2.0", "id": long_id, "method": "m"})
    log.log_response({"jsonrpc": "2.0", "id": long_id, "result": {}})
    entries = log.get_since(0)
    assert all(
        e["duration_ms"] is None for e in entries if e["direction"] == "response"
    )
