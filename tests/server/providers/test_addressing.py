"""Tests for the tool hashing primitives."""

from __future__ import annotations

from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    hash_tool,
    hashed_backend_name,
    hashed_resource_uri,
    parse_hashed_backend_name,
    parse_hashed_resource_uri,
)


class TestHashFunction:
    def test_hash_is_fixed_length_hex(self):
        h = hash_tool("myapp", "greet")
        assert len(h) == HASH_LENGTH
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_inputs_same_hash(self):
        a = hash_tool("app", "submit_form")
        b = hash_tool("app", "submit_form")
        assert a == b

    def test_different_app_names_different_hash(self):
        a = hash_tool("contacts", "save")
        b = hash_tool("billing", "save")
        assert a != b

    def test_different_tool_names_different_hash(self):
        a = hash_tool("app", "save")
        b = hash_tool("app", "delete")
        assert a != b


class TestBackendNameRoundtrip:
    def test_format_and_parse(self):
        name = hashed_backend_name("contacts", "submit_form")
        parsed = parse_hashed_backend_name(name)
        assert parsed is not None
        digest, local = parsed
        assert digest == hash_tool("contacts", "submit_form")
        assert local == "submit_form"

    def test_parse_rejects_short_strings(self):
        assert parse_hashed_backend_name("foo") is None

    def test_parse_rejects_non_hex_prefix(self):
        assert parse_hashed_backend_name("zzzzzzzzzzzz_save") is None

    def test_parse_rejects_missing_separator(self):
        assert parse_hashed_backend_name("abcdef012345save") is None


class TestResourceUriRoundtrip:
    def test_format_and_parse(self):
        uri = hashed_resource_uri("dashboard", "show")
        h = parse_hashed_resource_uri(uri)
        assert h == hash_tool("dashboard", "show")

    def test_parse_rejects_unrelated_uri(self):
        assert parse_hashed_resource_uri("file:///etc/passwd") is None

    def test_parse_rejects_wrong_length_hash(self):
        assert parse_hashed_resource_uri("ui://prefab/tool/abc/renderer.html") is None
