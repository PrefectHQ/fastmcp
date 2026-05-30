"""Tests for HTTP utility helpers."""

import socket

from fastmcp.utilities.http import find_available_port


def test_find_available_port_returns_bindable_loopback_port():
    port = find_available_port()

    assert isinstance(port, int)
    assert port > 0

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", port))
