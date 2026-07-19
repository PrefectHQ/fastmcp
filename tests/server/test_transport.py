import pytest

from fastmcp.server.mixins.transport import _format_host_for_url


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
