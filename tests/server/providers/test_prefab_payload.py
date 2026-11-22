"""Tests for re-addressing tool references in Prefab payloads."""

from __future__ import annotations

from fastmcp.server.providers.addressing import hash_tool
from fastmcp.server.providers.prefab_payload import rewrite_payload_tool_names


def _pre_4_2_payload(address: str) -> dict:
    """A payload as fastmcp 4.0-4.1 backends serialize it."""
    return {
        "view": {"action": "toolCall", "tool": address},
        "_meta": {"fastmcp": {"toolNames": {address: address}}},
    }


def test_pre_4_2_address_is_rebound_to_the_listed_name():
    identity = hash_tool("contacts", "save")
    payload = _pre_4_2_payload(f"{identity}_save")

    rewrite_payload_tool_names(payload, {identity: "up_save"}.get)

    assert payload["view"]["tool"] == "up_save"


def test_hash_shaped_name_without_a_matching_identity_is_unchanged():
    payload = _pre_4_2_payload("abcdef012345_save")

    rewrite_payload_tool_names(payload, {}.get)

    assert payload["view"]["tool"] == "abcdef012345_save"
