"""Tests for the Provenance Ledger."""

from __future__ import annotations

import pytest

from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.provenance.records import ProvenanceAction


class TestProvenanceLedgerBasics:
    def test_empty_ledger(self):
        ledger = ProvenanceLedger()
        assert ledger.record_count == 0
        assert ledger.latest_record is None
        assert ledger.get_chain_digest() == "empty"

    def test_record_event(self):
        ledger = ProvenanceLedger()
        record = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calculator",
            input_data={"expression": "2+2"},
        )
        assert record.action == ProvenanceAction.TOOL_CALLED
        assert record.actor_id == "agent-1"
        assert record.resource_id == "calculator"
        assert record.input_hash  # Non-empty
        assert record.previous_hash == "genesis"
        assert ledger.record_count == 1

    def test_chain_linking(self):
        ledger = ProvenanceLedger()
        r1 = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="a1",
            resource_id="tool1",
        )
        r2 = ledger.record(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="a1",
            resource_id="res1",
        )
        assert r1.previous_hash == "genesis"
        assert r2.previous_hash == r1.compute_hash()

    def test_get_record(self):
        ledger = ProvenanceLedger()
        record = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="a1",
            resource_id="tool1",
        )
        found = ledger.get_record(record.record_id)
        assert found is not None
        assert found.record_id == record.record_id

    def test_get_record_not_found(self):
        ledger = ProvenanceLedger()
        assert ledger.get_record("nonexistent") is None

    def test_latest_record(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t1"
        )
        r2 = ledger.record(
            action=ProvenanceAction.RESOURCE_READ, actor_id="a1", resource_id="r1"
        )
        assert ledger.latest_record.record_id == r2.record_id


class TestProvenanceLedgerQueries:
    def test_query_by_action(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t1"
        )
        ledger.record(
            action=ProvenanceAction.RESOURCE_READ, actor_id="a1", resource_id="r1"
        )
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t2"
        )

        results = ledger.get_records(action=ProvenanceAction.TOOL_CALLED)
        assert len(results) == 2

    def test_query_by_actor(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t1"
        )
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a2", resource_id="t1"
        )

        results = ledger.get_records(actor_id="a1")
        assert len(results) == 1

    def test_query_by_resource(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="calc"
        )
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="search"
        )

        results = ledger.get_records(resource_id="calc")
        assert len(results) == 1

    def test_query_with_limit(self):
        ledger = ProvenanceLedger()
        for i in range(10):
            ledger.record(
                action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id=f"t{i}"
            )

        results = ledger.get_records(limit=3)
        assert len(results) == 3

    def test_query_returns_most_recent_first(self):
        ledger = ProvenanceLedger()
        r1 = ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="first"
        )
        r2 = ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="second"
        )

        results = ledger.get_records()
        assert results[0].resource_id == "second"
        assert results[1].resource_id == "first"


class TestProvenanceLedgerChainIntegrity:
    def test_verify_chain_valid(self):
        ledger = ProvenanceLedger()
        for i in range(5):
            ledger.record(
                action=ProvenanceAction.TOOL_CALLED,
                actor_id="a1",
                resource_id=f"tool-{i}",
            )
        assert ledger.verify_chain()

    def test_verify_empty_chain(self):
        ledger = ProvenanceLedger()
        assert ledger.verify_chain()


class TestProvenanceLedgerMerkleIntegrity:
    def test_verify_tree(self):
        ledger = ProvenanceLedger()
        for i in range(5):
            ledger.record(
                action=ProvenanceAction.TOOL_CALLED,
                actor_id="a1",
                resource_id=f"tool-{i}",
            )
        assert ledger.verify_tree()

    def test_get_proof(self):
        ledger = ProvenanceLedger()
        records = []
        for i in range(5):
            r = ledger.record(
                action=ProvenanceAction.TOOL_CALLED,
                actor_id="a1",
                resource_id=f"tool-{i}",
            )
            records.append(r)

        # Verify proof for each record
        for r in records:
            proof = ledger.get_proof(r.record_id)
            assert proof.verify()

    def test_get_proof_not_found(self):
        ledger = ProvenanceLedger()
        with pytest.raises(KeyError):
            ledger.get_proof("nonexistent")

    def test_root_hash_changes(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t1"
        )
        root1 = ledger.root_hash

        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t2"
        )
        root2 = ledger.root_hash

        assert root1 != root2

    def test_chain_digest(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED, actor_id="a1", resource_id="t1"
        )
        digest = ledger.get_chain_digest()
        assert digest != "empty"
        assert len(digest) == 64


class TestProvenanceLedgerWithMetadata:
    def test_record_with_contract_id(self):
        ledger = ProvenanceLedger()
        record = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="a1",
            resource_id="t1",
            contract_id="contract-123",
        )
        assert record.contract_id == "contract-123"

    def test_record_with_output_data(self):
        ledger = ProvenanceLedger()
        record = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="a1",
            resource_id="t1",
            input_data={"x": 1},
            output_data={"result": 2},
        )
        assert record.input_hash
        assert record.output_hash
        assert record.input_hash != record.output_hash
