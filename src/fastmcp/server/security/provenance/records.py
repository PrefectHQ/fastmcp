"""Provenance record data models.

Defines the audit trail entries that capture who did what, when,
to which resource, and the cryptographic evidence linking records
into a tamper-evident chain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ProvenanceAction(Enum):
    """Types of actions recorded in the provenance ledger."""

    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    RESOURCE_READ = "resource_read"
    RESOURCE_LISTED = "resource_listed"
    PROMPT_RENDERED = "prompt_rendered"
    PROMPT_LISTED = "prompt_listed"
    POLICY_EVALUATED = "policy_evaluated"
    CONTRACT_CREATED = "contract_created"
    CONTRACT_REVOKED = "contract_revoked"
    ACCESS_DENIED = "access_denied"
    ERROR = "error"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ProvenanceRecord:
    """A single entry in the provenance ledger.

    Each record captures a discrete event in the system with full
    attribution and is hash-linked to its predecessor for tamper
    evidence.

    Attributes:
        record_id: Unique record identifier.
        action: What type of event occurred.
        actor_id: Who performed the action (agent, server, system).
        resource_id: What was acted upon (tool name, resource URI, etc.).
        timestamp: When the event occurred (UTC).
        input_hash: SHA-256 of the input/request data.
        output_hash: SHA-256 of the output/response data (if available).
        metadata: Additional context about the event.
        previous_hash: Hash of the preceding record in the chain.
        contract_id: Associated contract ID (if any).
        session_id: Session context.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action: ProvenanceAction = ProvenanceAction.CUSTOM
    actor_id: str = ""
    resource_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    input_hash: str = ""
    output_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = ""
    contract_id: str = ""
    session_id: str = ""

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this record for chain linking."""
        payload = {
            "record_id": self.record_id,
            "action": self.action.value,
            "actor_id": self.actor_id,
            "resource_id": self.resource_id,
            "timestamp": self.timestamp.isoformat(),
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "previous_hash": self.previous_hash,
            "contract_id": self.contract_id,
            "session_id": self.session_id,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage and transport."""
        return {
            "record_id": self.record_id,
            "action": self.action.value,
            "actor_id": self.actor_id,
            "resource_id": self.resource_id,
            "timestamp": self.timestamp.isoformat(),
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "contract_id": self.contract_id,
            "session_id": self.session_id,
        }


def hash_data(data: Any) -> str:
    """Compute SHA-256 hash of arbitrary data.

    Handles dicts (via canonical JSON), strings, bytes, and other types.
    """
    if isinstance(data, bytes):
        return hashlib.sha256(data).hexdigest()
    if isinstance(data, str):
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    if isinstance(data, dict):
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Fallback: repr
    return hashlib.sha256(repr(data).encode("utf-8")).hexdigest()
