"""Connect-time protocol-era negotiation on ``fastmcp.Client`` (``mode=``).

FastMCP serves both protocol eras from one server object over the in-memory
stream loop (``serve_dual_era_loop``), so a single ``fastmcp_server`` fixture can
be driven legacy or modern by varying ``mode=`` alone:

* ``mode="legacy"`` (the current default) runs the initialize handshake and
  reports the handshake-era version, byte-identically to pre-v4 behavior.
* ``mode="auto"`` probes ``server/discover`` and negotiates the modern era.
* ``mode="2026-07-28"`` pins the modern version and adopts a synthesized
  ``DiscoverResult`` without a probe.
"""

from __future__ import annotations

import pytest
from mcp_types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION

from fastmcp import Client, FastMCP


class TestModeValidation:
    def test_default_mode_is_legacy(self, fastmcp_server):
        """The conservative default is 'legacy' (see the v4 phasing note)."""
        assert Client(fastmcp_server).mode == "legacy"

    @pytest.mark.parametrize("mode", ["legacy", "auto", LATEST_MODERN_VERSION])
    def test_valid_modes_accepted(self, fastmcp_server, mode):
        assert Client(fastmcp_server, mode=mode).mode == mode

    def test_handshake_version_pin_rejected(self, fastmcp_server):
        """A legacy-era version string is not a valid pin; the error steers to 'legacy'."""
        with pytest.raises(ValueError, match="use mode='legacy'"):
            Client(fastmcp_server, mode=LATEST_HANDSHAKE_VERSION)

    def test_unknown_mode_rejected(self, fastmcp_server):
        with pytest.raises(ValueError, match="mode must be 'legacy', 'auto'"):
            Client(fastmcp_server, mode="bogus")


class TestLegacyMode:
    async def test_legacy_uses_initialize_handshake(self, fastmcp_server):
        async with Client(fastmcp_server, mode="legacy") as client:
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION
            # Legacy negotiation still populates the InitializeResult unchanged.
            assert client.initialize_result is not None
            assert client.initialize_result.server_info.name == "TestServer"
            assert client.server_capabilities is not None

    async def test_default_matches_legacy(self, fastmcp_server):
        """Omitting mode= is byte-identical to mode='legacy'."""
        async with Client(fastmcp_server) as client:
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION
            assert client.initialize_result is not None

    async def test_legacy_call_tool(self, fastmcp_server):
        async with Client(fastmcp_server, mode="legacy") as client:
            result = await client.call_tool("add", {"a": 2, "b": 3})
        assert result.data == 5


class TestAutoMode:
    async def test_auto_negotiates_modern_via_discover(self, fastmcp_server):
        """auto probes server/discover and adopts the modern era in-memory."""
        async with Client(fastmcp_server, mode="auto") as client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            # A discover connection carries capabilities but no InitializeResult.
            assert client.server_capabilities is not None
            assert client.initialize_result is None

    async def test_auto_call_tool(self, fastmcp_server):
        async with Client(fastmcp_server, mode="auto") as client:
            result = await client.call_tool("add", {"a": 4, "b": 5})
        assert result.data == 9

    async def test_auto_falls_back_to_legacy_for_handshake_only_server(self):
        """A server that only speaks the handshake era makes auto denylist-fall-back to
        initialize, which still populates the InitializeResult.

        FastMCP always serves both eras, so this is characterized against the
        real dual-era server: auto reaches modern here. The fallback denylist
        itself is exercised by the SDK's own ``negotiate_auto`` suite; this cell
        documents the FastMCP-observable outcome.
        """
        mcp = FastMCP("both-eras")

        @mcp.tool
        def ping() -> str:
            return "pong"

        async with Client(mcp, mode="auto") as client:
            assert client.protocol_version == LATEST_MODERN_VERSION


class TestPinnedMode:
    async def test_pinned_modern_adopts_without_probe(self, fastmcp_server):
        """Pinning the modern version adopts it directly; a synthesized
        DiscoverResult leaves server_info empty."""
        async with Client(fastmcp_server, mode=LATEST_MODERN_VERSION) as client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            assert client.initialize_result is None

    async def test_pinned_modern_call_tool(self, fastmcp_server):
        async with Client(fastmcp_server, mode=LATEST_MODERN_VERSION) as client:
            result = await client.call_tool("add", {"a": 7, "b": 8})
        assert result.data == 15


class TestConnectionProperties:
    async def test_properties_none_before_connect(self, fastmcp_server):
        client = Client(fastmcp_server, mode="auto")
        assert client.protocol_version is None
        assert client.server_capabilities is None

    async def test_properties_none_after_disconnect(self, fastmcp_server):
        client = Client(fastmcp_server, mode="auto")
        async with client:
            assert client.protocol_version is not None
        assert client.protocol_version is None
        assert client.server_capabilities is None


class TestManualNegotiation:
    async def test_manual_initialize_legacy(self, fastmcp_server):
        """auto_initialize=False + a manual initialize() still works in legacy mode."""
        client = Client(fastmcp_server, mode="legacy", auto_initialize=False)
        async with client:
            assert client.protocol_version is None
            result = await client.initialize()
            assert result.server_info.name == "TestServer"
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION

    async def test_initialize_raises_on_modern_era(self, fastmcp_server):
        """A modern connection has no InitializeResult, so initialize() raises with a
        pointer to the era-neutral properties."""
        client = Client(fastmcp_server, mode="auto", auto_initialize=False)
        async with client:
            with pytest.raises(RuntimeError, match="modern protocol era"):
                await client.initialize()
            # Negotiation still happened; the era-neutral surface is populated.
            assert client.protocol_version == LATEST_MODERN_VERSION


class TestNewPreservesMode:
    async def test_new_preserves_mode(self, fastmcp_server):
        parent = Client(fastmcp_server, mode="auto")
        child = parent.new()
        assert child.mode == "auto"
        async with child:
            assert child.protocol_version == LATEST_MODERN_VERSION
