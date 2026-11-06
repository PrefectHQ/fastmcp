"""Tests for the tool hashing primitives."""

from __future__ import annotations

import pytest

from fastmcp.server.providers.addressing import (
    HASH_LENGTH,
    hash_tool,
    hashed_backend_name,
    hashed_resource_uri,
    is_public_backend_address,
    parse_hashed_backend_name,
    parse_hashed_resource_uri,
    public_backend_name,
)
from fastmcp.tools.base import Tool


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
    def test_legacy_proxy_metadata_still_owns_canonical_address(self):
        address = hashed_backend_name("contacts", "save")
        tool = Tool.from_function(
            lambda: None,
            name=address,
            meta={
                "fastmcp": {
                    "app": "contacts",
                    "tool_hash": hash_tool("contacts", "save"),
                },
                "ui": {"visibility": ["app"]},
            },
        )
        assert is_public_backend_address(tool, address)

    def test_mismatched_original_name_does_not_own_canonical_address(self):
        address = hashed_backend_name("contacts", "save")
        tool = Tool.from_function(
            lambda: None,
            name=address,
            meta={
                "fastmcp": {
                    "app": "contacts",
                    "tool_hash": hash_tool("contacts", "save"),
                    "tool_name": "delete",
                },
                "ui": {"visibility": ["app"]},
            },
        )
        assert not is_public_backend_address(tool, address)

    @pytest.mark.parametrize(
        "fastmcp_meta",
        [
            None,
            {"app": "contacts", "tool_name": "save", "tool_hash": "wrong"},
            {
                "app": 123,
                "tool_name": "save",
                "tool_hash": hash_tool("contacts", "save"),
            },
            {
                "app": "contacts",
                "tool_name": 123,
                "tool_hash": hash_tool("contacts", "save"),
            },
        ],
    )
    def test_public_name_rejects_invalid_metadata(self, fastmcp_meta):
        tool = Tool.from_function(
            lambda: None,
            name="save",
            meta={"fastmcp": fastmcp_meta, "ui": {"visibility": ["app"]}},
        )
        assert public_backend_name(tool) is None

    def test_format_and_parse(self):
        name = hashed_backend_name("contacts", "submit_form")
        assert name == f"__{hash_tool('contacts', 'submit_form')}_submit_form"
        parsed = parse_hashed_backend_name(name)
        assert parsed is not None
        digest, local = parsed
        assert digest == hash_tool("contacts", "submit_form")
        assert local == "submit_form"

    def test_parse_requires_double_underscore_prefix(self):
        assert parse_hashed_backend_name("abcdef012345_save") is None
        assert parse_hashed_backend_name("_abcdef012345_save") is None

    def test_parse_rejects_short_strings(self):
        assert parse_hashed_backend_name("foo") is None

    def test_parse_rejects_non_hex_prefix(self):
        assert parse_hashed_backend_name("__zzzzzzzzzzzz_save") is None

    def test_parse_rejects_missing_separator(self):
        assert parse_hashed_backend_name("__abcdef012345save") is None


class TestResourceUriRoundtrip:
    def test_format_and_parse(self):
        uri = hashed_resource_uri("dashboard", "show")
        h = parse_hashed_resource_uri(uri)
        assert h == hash_tool("dashboard", "show")

    def test_parse_rejects_unrelated_uri(self):
        assert parse_hashed_resource_uri("file:///etc/passwd") is None

    def test_parse_rejects_wrong_length_hash(self):
        assert parse_hashed_resource_uri("ui://prefab/tool/abc/renderer.html") is None
