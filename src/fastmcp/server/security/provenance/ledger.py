"""Provenance Ledger for SecureMCP.

Central audit trail that records all MCP operations with hash-chain
integrity and Merkle tree verification. Provides efficient queries
and tamper detection for the full operation history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.provenance.merkle import MerkleTree
from fastmcp.server.security.provenance.records import (
    ProvenanceAction,
    ProvenanceRecord,
    hash_data,
)

logger = logging.getLogger(__name__)


class ProvenanceLedger:
    """Append-only provenance ledger with hash-chain and Merkle tree integrity.

    Every operation recorded gets:
    1. A hash-chained link to the previous record (sequential integrity)
    2. A Merkle tree leaf for efficient batch verification

    Example::

        ledger = ProvenanceLedger()

        record = ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calculator",
            input_data={"expression": "2+2"},
        )

        # Verify chain integrity
        assert ledger.verify_chain()

        # Verify Merkle tree
        assert ledger.verify_tree()

        # Get proof for a specific record
        proof = ledger.get_proof(record.record_id)
        assert proof.verify()

    Args:
        ledger_id: Unique identifier for this ledger instance.
    """

    def __init__(self, ledger_id: str = "default") -> None:
        self.ledger_id = ledger_id
        self._records: list[ProvenanceRecord] = []
        self._record_index: dict[str, int] = {}
        self._merkle_tree = MerkleTree()

    def record(
        self,
        action: ProvenanceAction,
        actor_id: str,
        resource_id: str = "",
        *,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict[str, Any] | None = None,
        contract_id: str = "",
        session_id: str = "",
    ) -> ProvenanceRecord:
        """Record a provenance event.

        Args:
            action: Type of action being recorded.
            actor_id: Who performed the action.
            resource_id: What was acted upon.
            input_data: Input/request data (will be hashed).
            output_data: Output/response data (will be hashed).
            metadata: Additional context.
            contract_id: Associated contract.
            session_id: Session context.

        Returns:
            The recorded ProvenanceRecord with computed hashes.
        """
        # Compute content hashes
        input_hash = hash_data(input_data) if input_data is not None else ""
        output_hash = hash_data(output_data) if output_data is not None else ""

        # Chain to previous record
        previous_hash = (
            self._records[-1].compute_hash() if self._records else "genesis"
        )

        entry = ProvenanceRecord(
            action=action,
            actor_id=actor_id,
            resource_id=resource_id,
            input_hash=input_hash,
            output_hash=output_hash,
            metadata=metadata or {},
            previous_hash=previous_hash,
            contract_id=contract_id,
            session_id=session_id,
        )

        # Store and index
        idx = len(self._records)
        self._records.append(entry)
        self._record_index[entry.record_id] = idx

        # Add to Merkle tree
        self._merkle_tree.add_leaf(entry.compute_hash())

        logger.debug(
            "Provenance: recorded %s by %s on %s (record %s)",
            action.value,
            actor_id,
            resource_id,
            entry.record_id[:8],
        )

        return entry

    def get_record(self, record_id: str) -> ProvenanceRecord | None:
        """Look up a record by ID."""
        idx = self._record_index.get(record_id)
        if idx is None:
            return None
        return self._records[idx]

    def get_records(
        self,
        *,
        action: ProvenanceAction | None = None,
        actor_id: str | None = None,
        resource_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ProvenanceRecord]:
        """Query records with optional filters.

        Args:
            action: Filter by action type.
            actor_id: Filter by actor.
            resource_id: Filter by resource.
            since: Only records after this timestamp.
            until: Only records before this timestamp.
            limit: Maximum number of records to return.

        Returns:
            List of matching records, most recent first.
        """
        results: list[ProvenanceRecord] = []

        for rec in reversed(self._records):
            if action is not None and rec.action != action:
                continue
            if actor_id is not None and rec.actor_id != actor_id:
                continue
            if resource_id is not None and rec.resource_id != resource_id:
                continue
            if since is not None and rec.timestamp < since:
                continue
            if until is not None and rec.timestamp > until:
                continue
            results.append(rec)
            if len(results) >= limit:
                break

        return results

    def verify_chain(self) -> bool:
        """Verify the hash chain integrity of all records.

        Returns True if the chain is intact, False if tampered.
        """
        if not self._records:
            return True

        # First record should link to genesis
        if self._records[0].previous_hash != "genesis":
            logger.warning("Provenance chain: invalid genesis link")
            return False

        # Each subsequent record should link to the hash of its predecessor
        for i in range(1, len(self._records)):
            expected = self._records[i - 1].compute_hash()
            if self._records[i].previous_hash != expected:
                logger.warning("Provenance chain: broken at record %d", i)
                return False

        return True

    def verify_tree(self) -> bool:
        """Verify the Merkle tree integrity."""
        return self._merkle_tree.verify_tree()

    def get_proof(self, record_id: str) -> Any:
        """Get a Merkle inclusion proof for a record.

        Args:
            record_id: The record to prove.

        Returns:
            MerkleProof for the record.

        Raises:
            KeyError: If record_id not found.
        """
        idx = self._record_index.get(record_id)
        if idx is None:
            raise KeyError(f"Record '{record_id}' not found in ledger")
        return self._merkle_tree.get_proof(idx)

    @property
    def root_hash(self) -> str:
        """Current Merkle root hash summarizing all records."""
        return self._merkle_tree.root_hash

    @property
    def record_count(self) -> int:
        """Total number of records in the ledger."""
        return len(self._records)

    @property
    def latest_record(self) -> ProvenanceRecord | None:
        """The most recently recorded entry."""
        return self._records[-1] if self._records else None

    def get_chain_digest(self) -> str:
        """Get a summary digest of the entire chain.

        Returns the hash of the last record, which transitively
        depends on all previous records.
        """
        if not self._records:
            return "empty"
        return self._records[-1].compute_hash()
