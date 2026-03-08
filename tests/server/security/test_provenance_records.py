"""Tests for provenance record data models."""

from __future__ import annotations

from fastmcp.server.security.provenance.records import (
    ProvenanceAction,
    ProvenanceRecord,
    hash_data,
)


class TestProvenanceRecord:
    def test_default_record(self):
        record = ProvenanceRecord()
        assert record.action == ProvenanceAction.CUSTOM
        assert record.actor_id == ""
        assert record.previous_hash == ""

    def test_record_with_values(self):
        record = ProvenanceRecord(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calculator",
            input_hash="abc123",
        )
        assert record.action == ProvenanceAction.TOOL_CALLED
        assert record.actor_id == "agent-1"

    def test_compute_hash_deterministic(self):
        record = ProvenanceRecord(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calculator",
        )
        h1 = record.compute_hash()
        h2 = record.compute_hash()
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_records_different_hashes(self):
        r1 = ProvenanceRecord(actor_id="a1")
        r2 = ProvenanceRecord(actor_id="a2")
        assert r1.compute_hash() != r2.compute_hash()

    def test_to_dict(self):
        record = ProvenanceRecord(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="agent-1",
            resource_id="file://data.csv",
            metadata={"size": 1024},
        )
        d = record.to_dict()
        assert d["action"] == "resource_read"
        assert d["actor_id"] == "agent-1"
        assert d["metadata"]["size"] == 1024


class TestHashData:
    def test_hash_string(self):
        h = hash_data("hello")
        assert len(h) == 64

    def test_hash_bytes(self):
        h = hash_data(b"hello")
        assert len(h) == 64

    def test_hash_dict(self):
        h = hash_data({"key": "value"})
        assert len(h) == 64

    def test_hash_dict_order_independent(self):
        h1 = hash_data({"b": 2, "a": 1})
        h2 = hash_data({"a": 1, "b": 2})
        assert h1 == h2

    def test_hash_other_types(self):
        h = hash_data(42)
        assert len(h) == 64
