"""Tests for the provider addressing primitives.

Covers the deterministic hash function, address registry walker, and
reverse-hash map. The address registry assigns positional integer
indices to every mount point in a FastMCP server's provider graph; the
reverse-hash map maps the resulting hashes back to specific tools so
``read_resource`` and the backend-tool dispatcher can do O(1) lookups.
"""

from __future__ import annotations

from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    build_address_registry,
    build_reverse_hash_map,
    hash_tool_address,
    hashed_backend_name,
    hashed_resource_uri,
    parse_hashed_backend_name,
    parse_hashed_resource_uri,
)


class TestHashFunction:
    def test_hash_is_fixed_length_hex(self):
        h = hash_tool_address((), "greet")
        assert len(h) == HASH_LENGTH
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_inputs_same_hash(self):
        a = hash_tool_address((0, 1), "submit_form")
        b = hash_tool_address((0, 1), "submit_form")
        assert a == b

    def test_different_addresses_different_hash(self):
        a = hash_tool_address((0,), "submit")
        b = hash_tool_address((1,), "submit")
        assert a != b

    def test_different_tool_names_different_hash(self):
        a = hash_tool_address((0,), "save")
        b = hash_tool_address((0,), "delete")
        assert a != b

    def test_address_segment_boundaries_matter(self):
        """``("ab", "c")`` and ``("a", "bc")`` would collide under naive
        concatenation; the hash function uses an unambiguous encoding."""
        a = hash_tool_address((0, 12), "save")
        b = hash_tool_address((0, 1), "2save")
        assert a != b


class TestBackendNameRoundtrip:
    def test_format_and_parse(self):
        name = hashed_backend_name((0, 2), "submit_form")
        parsed = parse_hashed_backend_name(name)
        assert parsed is not None
        digest, local = parsed
        assert digest == hash_tool_address((0, 2), "submit_form")
        assert local == "submit_form"

    def test_parse_rejects_short_strings(self):
        assert parse_hashed_backend_name("foo") is None

    def test_parse_rejects_non_hex_prefix(self):
        # Right shape, wrong characters in the prefix.
        assert parse_hashed_backend_name("zzzzzzzzzzzz_save") is None

    def test_parse_rejects_missing_separator(self):
        # 12 hex chars but no underscore at position 12.
        assert parse_hashed_backend_name("abcdef012345save") is None


class TestResourceUriRoundtrip:
    def test_format_and_parse(self):
        uri = hashed_resource_uri((0, 1), "show_dashboard")
        h = parse_hashed_resource_uri(uri)
        assert h == hash_tool_address((0, 1), "show_dashboard")

    def test_parse_rejects_unrelated_uri(self):
        assert parse_hashed_resource_uri("file:///etc/passwd") is None

    def test_parse_rejects_wrong_length_hash(self):
        assert parse_hashed_resource_uri("ui://prefab/tool/abc/renderer.html") is None


class TestAddressRegistry:
    def test_empty_server_has_root_entry(self):
        mcp = FastMCP("server")
        registry = build_address_registry(mcp)
        # The root's local provider lives at the empty path.
        assert () in registry
        assert registry[()] is mcp._local_provider

    def test_root_tool_is_at_empty_path(self):
        mcp = FastMCP("server")

        @mcp.tool
        def greet(name: str) -> str:
            return name

        registry = build_address_registry(mcp)
        # Root LocalProvider is transparent — no extra segment.
        assert registry[()] is mcp._local_provider
        assert len(registry) == 1

    def test_added_provider_gets_positional_index(self):
        mcp = FastMCP("server")
        app = FastMCPApp("dashboard")
        mcp.add_provider(app)
        registry = build_address_registry(mcp)
        # First non-transparent child is at (0,).
        assert (0,) in registry
        assert registry[(0,)] is app

    def test_multiple_providers_indexed_by_registration_order(self):
        mcp = FastMCP("server")
        a = FastMCPApp("a")
        b = FastMCPApp("b")
        c = FastMCPApp("c")
        mcp.add_provider(a)
        mcp.add_provider(b)
        mcp.add_provider(c)
        registry = build_address_registry(mcp)
        assert registry[(0,)] is a
        assert registry[(1,)] is b
        assert registry[(2,)] is c

    def test_walker_is_deterministic_across_replicas(self):
        """Same code, same addresses — required for horizontal sharding."""

        def make_server() -> FastMCP:
            m = FastMCP("server")
            m.add_provider(FastMCPApp("first"))
            m.add_provider(FastMCPApp("second"))
            return m

        r1 = build_address_registry(make_server())
        r2 = build_address_registry(make_server())
        assert set(r1.keys()) == set(r2.keys())

    def test_same_provider_mounted_twice_appears_at_two_addresses(self):
        mcp = FastMCP("server")
        shared = FastMCPApp("shared")
        mcp.add_provider(shared)
        mcp.add_provider(shared)
        registry = build_address_registry(mcp)
        assert registry[(0,)] is shared
        assert registry[(1,)] is shared


class TestReverseHashMap:
    def test_includes_root_tools_at_empty_address(self):
        mcp = FastMCP("server")

        @mcp.tool
        def greet(name: str) -> str:
            return name

        maps = build_reverse_hash_map(build_address_registry(mcp))
        digest = hash_tool_address((), "greet")
        assert digest in maps.by_hash
        entry = maps.by_hash[digest]
        assert entry.address == ()
        assert entry.tool_name == "greet"

    def test_includes_app_tools_at_app_address(self):
        mcp = FastMCP("server")
        app = FastMCPApp("dashboard")
        mcp.add_provider(app)

        @app.tool()
        def save(name: str) -> str:
            return name

        maps = build_reverse_hash_map(build_address_registry(mcp))
        digest = hash_tool_address((0,), "save")
        assert digest in maps.by_hash
        entry = maps.by_hash[digest]
        assert entry.address == (0,)
        assert entry.tool_name == "save"
        assert entry.provider is app

    def test_callable_map_indexes_by_fn_identity(self):
        """The callable map lets the resolver look up a tool by function
        identity — no name ambiguity, no mount_path needed."""
        mcp = FastMCP("server")
        app = FastMCPApp("dashboard")
        mcp.add_provider(app)

        @app.tool()
        def save(name: str) -> str:
            return name

        maps = build_reverse_hash_map(build_address_registry(mcp))
        assert id(save) in maps.by_callable
        assert maps.by_callable[id(save)].tool_name == "save"

    def test_same_tool_at_two_addresses_has_two_hashes(self):
        """Mounting the same FastMCPApp instance twice produces distinct
        hashes for the same underlying tool — one per mount address."""
        mcp = FastMCP("server")
        shared = FastMCPApp("shared")

        @shared.tool()
        def save(name: str) -> str:
            return name

        mcp.add_provider(shared)
        mcp.add_provider(shared)

        maps = build_reverse_hash_map(build_address_registry(mcp))
        h1 = hash_tool_address((0,), "save")
        h2 = hash_tool_address((1,), "save")
        assert h1 != h2
        assert h1 in maps.by_hash
        assert h2 in maps.by_hash
        # Both entries point at the same provider object.
        assert maps.by_hash[h1].provider is maps.by_hash[h2].provider


class TestServerLazyAddressRegistry:
    def test_registry_cached_after_first_access(self):
        mcp = FastMCP("server")
        r1 = mcp.address_registry
        r2 = mcp.address_registry
        assert r1 is r2

    def test_add_provider_invalidates_registry_and_reverse_map(self):
        mcp = FastMCP("server")
        r1 = mcp.address_registry
        rev1 = mcp.reverse_hash_map
        mcp.add_provider(FastMCPApp("dashboard"))
        r2 = mcp.address_registry
        rev2 = mcp.reverse_hash_map
        assert r1 is not r2
        assert rev1 is not rev2
        assert (0,) in r2
