"""Contract data models for the Context Broker.

Defines the core types used in contract negotiation:
- ContractTerm: individual constraint or obligation
- Contract: collection of terms with metadata and signatures
- ContractNegotiationRequest/Response: negotiation protocol messages
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class ContractStatus(Enum):
    """Lifecycle status of a contract."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    COUNTER_PROPOSED = "counter_proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class NegotiationStatus(Enum):
    """Status of a negotiation round."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COUNTER = "counter"


class TermType(Enum):
    """Category of contract term."""

    DATA_USAGE = "data_usage"
    RETENTION = "retention"
    ACCESS_CONTROL = "access_control"
    RATE_LIMIT = "rate_limit"
    AUDIT = "audit"
    COMPLIANCE = "compliance"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ContractTerm:
    """A single constraint or obligation in a contract.

    Terms define what is allowed, required, or prohibited within the
    scope of the contract. They form the atomic units of negotiation.

    Attributes:
        term_id: Unique identifier for this term.
        term_type: Category of the term.
        description: Human-readable description.
        constraint: Machine-readable constraint definition.
        required: If True, this term cannot be removed during negotiation.
        metadata: Additional term-specific data.
    """

    term_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    term_type: TermType = TermType.CUSTOM
    description: str = ""
    constraint: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Contract:
    """A contract between an agent and a server.

    Contracts are negotiated before tool execution and define the terms
    under which operations may proceed. They are cryptographically signed
    for non-repudiation.

    Attributes:
        contract_id: Unique identifier.
        session_id: The negotiation session that produced this contract.
        server_id: The MCP server identity.
        agent_id: The requesting agent/model identity.
        terms: The agreed-upon terms.
        status: Current lifecycle status.
        created_at: When the contract was created.
        expires_at: When the contract expires (None = no expiry).
        signatures: Cryptographic signatures from parties.
        version: Contract version (incremented on amendments).
        parent_id: ID of the contract this amends (if any).
        metadata: Additional contract-level data.
    """

    contract_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    server_id: str = ""
    agent_id: str = ""
    terms: list[ContractTerm] = field(default_factory=list)
    status: ContractStatus = ContractStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    signatures: dict[str, str] = field(default_factory=dict)
    version: int = 1
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Check if the contract is currently valid (active and not expired)."""
        if self.status != ContractStatus.ACTIVE:
            return False
        if self.expires_at is not None:
            return datetime.now(timezone.utc) < self.expires_at
        return True

    def is_signed_by(self, party: str) -> bool:
        """Check if a specific party has signed this contract."""
        return party in self.signatures

    def set_default_expiry(self, duration: timedelta = timedelta(hours=1)) -> None:
        """Set expiry relative to creation time."""
        self.expires_at = self.created_at + duration

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for signing and transport."""
        return {
            "contract_id": self.contract_id,
            "session_id": self.session_id,
            "server_id": self.server_id,
            "agent_id": self.agent_id,
            "terms": [
                {
                    "term_id": t.term_id,
                    "term_type": t.term_type.value,
                    "description": t.description,
                    "constraint": t.constraint,
                    "required": t.required,
                    "metadata": t.metadata,
                }
                for t in self.terms
            ],
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "version": self.version,
            "parent_id": self.parent_id,
            "metadata": self.metadata,
        }


@dataclass
class ContractNegotiationRequest:
    """A request to initiate or continue contract negotiation.

    Attributes:
        request_id: Unique request identifier.
        session_id: Existing session to continue, or empty for new session.
        agent_id: The agent making the request.
        proposed_terms: Terms the agent proposes.
        context: Additional context about the request.
    """

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    agent_id: str = ""
    proposed_terms: list[ContractTerm] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractNegotiationResponse:
    """Response to a contract negotiation request.

    Attributes:
        request_id: The request this responds to.
        session_id: The negotiation session ID.
        status: Outcome of this negotiation round.
        contract: The resulting contract (if accepted).
        counter_terms: Counter-proposed terms (if status is COUNTER).
        reason: Explanation for rejection or counter-proposal.
    """

    request_id: str = ""
    session_id: str = ""
    status: NegotiationStatus = NegotiationStatus.PENDING
    contract: Contract | None = None
    counter_terms: list[ContractTerm] | None = None
    reason: str = ""
