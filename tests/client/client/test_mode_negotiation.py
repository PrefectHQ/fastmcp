"""Connect-time protocol-era negotiation on ``fastmcp.Client`` (``mode=``).

FastMCP serves both protocol eras from one server object over the in-memory
stream loop (``serve_dual_era_loop``), so a single ``fastmcp_server`` fixture can
be driven legacy or modern by varying ``mode=`` alone:

* ``mode="auto"`` (the default) probes ``server/discover`` and negotiates the
  modern era, denylist-falling-back to the initialize handshake for any server
  that is not positive evidence of a modern peer.
* ``mode="legacy"`` runs the initialize handshake and reports the handshake-era
  version, byte-identically to pre-v4 behavior.
* ``mode="2026-07-28"`` pins the modern version and adopts a synthesized
  ``DiscoverResult`` without a probe.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import ClientSession
from mcp.shared.exceptions import MCPError
from mcp_types import METHOD_NOT_FOUND
from mcp_types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION
from typing_extensions import Unpack

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport, SessionKwargs


class TestModeValidation:
    def test_default_mode_is_auto(self, fastmcp_server):
        """The default is 'auto': probe server/discover, fall back to the handshake."""
        assert Client(fastmcp_server).mode == "auto"

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

    async def test_default_matches_auto(self, fastmcp_server):
        """Omitting mode= is identical to mode='auto': modern via server/discover."""
        async with Client(fastmcp_server) as client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            assert client.initialize_result is None

    async def test_auto_reaches_modern_for_dual_era_server(self):
        """FastMCP always serves both eras, so auto reaches modern here.

        The fallback denylist itself is exercised by the SDK's own
        ``negotiate_auto`` suite; this cell documents the FastMCP-observable
        outcome for a plain server.
        """
        mcp = FastMCP("both-eras")

        @mcp.tool
        def ping() -> str:
            return "pong"

        async with Client(mcp, mode="auto") as client:
            assert client.protocol_version == LATEST_MODERN_VERSION

    async def test_auto_falls_back_cleanly_when_discover_is_rejected(
        self, fastmcp_server
    ):
        """A server that rejects the server/discover probe with a JSON-RPC error
        (e.g. a non-FastMCP legacy server that doesn't implement discover) makes
        auto fall back to the initialize handshake, cleanly — no error surfaces
        and the legacy InitializeResult is populated.

        This characterizes the FastMCP-observable outcome of the SDK's
        denylist fallback (`negotiate_auto`): every RPC error except a
        disjoint modern-only -32022 falls back to `initialize()`.
        """

        class _DiscoverRejectingTransport(FastMCPTransport):
            """Wraps the in-memory transport but rejects server/discover."""

            @contextlib.asynccontextmanager
            async def connect_session(
                self, **session_kwargs: Unpack[SessionKwargs]
            ) -> AsyncIterator[ClientSession]:
                async with super().connect_session(**session_kwargs) as session:

                    async def _reject_discover(version: str) -> dict[str, Any]:
                        raise MCPError(
                            code=METHOD_NOT_FOUND, message="Method not found"
                        )

                    session.send_discover = _reject_discover  # ty: ignore[invalid-assignment]
                    yield session

        transport = _DiscoverRejectingTransport(fastmcp_server)
        async with Client(transport, mode="auto") as client:
            # Fell back to the handshake: legacy version + populated InitializeResult.
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION
            assert client.initialize_result is not None
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.data == 3

    async def test_auto_uses_legacy_on_legacy_only_transport(self, fastmcp_server):
        """A `legacy_only` transport (e.g. SSE) negotiates the handshake under auto.

        SSE cannot serve the sessionless modern era, so a client with the default
        `mode="auto"` must run the initialize handshake directly rather than
        probing server/discover (which the FastMCP server answers even over SSE
        but then cannot serve).
        """

        class _LegacyOnlyTransport(FastMCPTransport):
            legacy_only = True

        transport = _LegacyOnlyTransport(fastmcp_server)
        async with Client(transport, mode="auto") as client:
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION
            assert client.initialize_result is not None


class TestNonConformantModernPeer:
    """A peer that answers ``server/discover`` but cannot actually serve the era.

    ``negotiate_auto`` accepts a probe that parses as the version-free
    ``DiscoverResult``, where ``resultType``/``ttlMs``/``cacheScope`` all carry
    SDK-side defaults. Every request after adoption is checked against the strict
    per-version surface, where those three fields are required. Left alone, a
    server that omits them on ``server/discover`` passes the probe and then fails
    every subsequent call, so ``auto`` would adopt an era the peer cannot serve.

    GitHub's remote MCP server is a live example: it has adopted the SEP-2549
    cache fields but not result tagging, so it answers ``server/discover``
    with ``ttlMs``/``cacheScope`` and no ``resultType``.
    """

    @staticmethod
    def _discover_body(**envelope: Any) -> dict[str, Any]:
        return {
            "supportedVersions": [LATEST_MODERN_VERSION],
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "TestServer", "version": "1.0"},
            **envelope,
        }

    @staticmethod
    def _transport_answering(body: dict[str, Any], server) -> FastMCPTransport:
        """An in-memory transport whose ``server/discover`` returns ``body`` verbatim."""

        class _FixedDiscoverTransport(FastMCPTransport):
            @contextlib.asynccontextmanager
            async def connect_session(
                self, **session_kwargs: Unpack[SessionKwargs]
            ) -> AsyncIterator[ClientSession]:
                async with super().connect_session(**session_kwargs) as session:

                    async def _fixed_discover(version: str) -> dict[str, Any]:
                        return body

                    session.send_discover = _fixed_discover  # ty: ignore[invalid-assignment]
                    yield session

        return _FixedDiscoverTransport(server)

    @pytest.mark.parametrize(
        "envelope",
        [
            pytest.param({}, id="no-envelope-fields"),
            pytest.param(
                {"ttlMs": 0, "cacheScope": "private"}, id="github-shape-no-resultType"
            ),
            pytest.param({"resultType": "complete"}, id="no-cache-fields"),
        ],
    )
    async def test_non_conformant_discover_falls_back_to_handshake(
        self, fastmcp_server, envelope
    ):
        """A discover result missing required 2026-07-28 fields is not modern evidence.

        Rather than adopting an era the peer cannot serve, auto degrades to the
        initialize handshake and the connection stays fully usable.
        """
        transport = self._transport_answering(
            self._discover_body(**envelope), fastmcp_server
        )
        async with Client(transport, mode="auto") as client:
            assert client.protocol_version == LATEST_HANDSHAKE_VERSION
            assert client.initialize_result is not None
            # The connection works, which is the whole point of degrading.
            assert await client.list_tools()
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.data == 3

    async def test_conformant_discover_still_adopts_modern(self, fastmcp_server):
        """The conformance check must not reject a well-formed modern peer."""
        transport = self._transport_answering(
            self._discover_body(resultType="complete", ttlMs=0, cacheScope="private"),
            fastmcp_server,
        )
        async with Client(transport, mode="auto") as client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            assert client.initialize_result is None
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.data == 3


class TestPinnedMode:
    async def test_pinned_modern_adopts_without_probe(self, fastmcp_server):
        """Pinning the modern version adopts it directly; a synthesized
        DiscoverResult leaves server_info empty."""
        async with Client(fastmcp_server, mode=LATEST_MODERN_VERSION) as client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            assert client.initialize_result is None
            assert client.server_info is not None
            assert client.server_info.name == ""
            assert client.instructions is None

    async def test_pinned_modern_call_tool(self, fastmcp_server):
        async with Client(fastmcp_server, mode=LATEST_MODERN_VERSION) as client:
            result = await client.call_tool("add", {"a": 7, "b": 8})
        assert result.data == 15


class TestConnectionProperties:
    @pytest.mark.parametrize("mode", ["legacy", "auto"])
    async def test_server_metadata_available_across_eras(self, mode):
        server = FastMCP("MetadataServer", instructions="Use the metadata tools.")
        async with Client(server, mode=mode) as client:
            assert client.server_info is not None
            assert client.server_info.name == "MetadataServer"
            assert client.instructions == "Use the metadata tools."

    async def test_properties_none_before_connect(self, fastmcp_server):
        client = Client(fastmcp_server, mode="auto")
        assert client.protocol_version is None
        assert client.server_capabilities is None
        assert client.server_info is None
        assert client.instructions is None

    async def test_properties_none_after_disconnect(self, fastmcp_server):
        client = Client(fastmcp_server, mode="auto")
        async with client:
            assert client.protocol_version is not None
        assert client.protocol_version is None
        assert client.server_capabilities is None
        assert client.server_info is None
        assert client.instructions is None


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
