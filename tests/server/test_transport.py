import pytest

from fastmcp.server.mixins.transport import (
    _format_host_for_url,
    _resolve_allowed_hosts_for_run,
)


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
        ("0.0.0.0", "0.0.0.0"),
        ("::1", "[::1]"),
        ("::", "[::]"),
        ("fe80::1", "[fe80::1]"),
        ("[::1]", "[::1]"),
    ],
)
def test_format_host_for_url(host: str, expected: str):
    """IPv6 hosts are bracketed for use in a URL; everything else is unchanged."""
    assert _format_host_for_url(host) == expected


def test_resolve_allowed_hosts_for_run_merges_configured_hosts_with_loopback_host():
    assert _resolve_allowed_hosts_for_run(
        host="127.0.0.1",
        host_origin_protection="auto",
        allowed_hosts=None,
        configured_allowed_hosts=["mcp.example.com"],
    ) == ["mcp.example.com", "127.0.0.1"]


def test_resolve_allowed_hosts_for_run_preserves_configured_hosts_when_disabled():
    assert _resolve_allowed_hosts_for_run(
        host="127.0.0.1",
        host_origin_protection=False,
        allowed_hosts=None,
        configured_allowed_hosts=["mcp.example.com"],
    ) == ["mcp.example.com"]


def test_resolve_allowed_hosts_for_run_preserves_explicit_hosts():
    assert _resolve_allowed_hosts_for_run(
        host="127.0.0.1",
        host_origin_protection="auto",
        allowed_hosts=["mcp.example.com"],
        configured_allowed_hosts=["settings.example.com"],
    ) == ["mcp.example.com"]
