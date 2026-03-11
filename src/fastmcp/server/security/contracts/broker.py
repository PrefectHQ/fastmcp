"""Context Broker for SecureMCP.

Manages contract negotiation sessions between agents and servers.
Handles the full lifecycle: initiate → propose → counter → accept/reject.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    compute_digest,
)
from fastmcp.server.security.contracts.exchange_log import (
    ExchangeEventType,
    ExchangeLog,
)
from fastmcp.server.security.contracts.schema import (
    Contract,
    ContractNegotiationRequest,
    ContractNegotiationResponse,
    ContractStatus,
    ContractTerm,
    NegotiationStatus,
)
from fastmcp.server.security.storage.backend import StorageBackend

logger = logging.getLogger(__name__)


@dataclass
class NegotiationSession:
    """State of an ongoing negotiation session.

    Attributes:
        session_id: Unique session identifier.
        agent_id: The agent negotiating.
        server_id: The server identity.
        current_terms: The latest set of terms under negotiation.
        round_count: How many negotiation rounds have occurred.
        created_at: When the session started.
        last_activity: When the last action occurred.
        status: Current session status.
        contract: The resulting contract (once accepted).
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    server_id: str = ""
    current_terms: list[ContractTerm] = field(default_factory=list)
    round_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: NegotiationStatus = NegotiationStatus.PENDING
    contract: Contract | None = None


class ContextBroker:
    """Manages contract negotiation between agents and servers.

    The broker coordinates the negotiation protocol:
    1. Agent sends a ``ContractNegotiationRequest`` with proposed terms
    2. Server evaluates terms against its policy (via ``term_evaluator``)
    3. Server accepts, rejects, or counter-proposes
    4. On acceptance, contract is created and optionally signed

    Example::

        from fastmcp.server.security.contracts import (
            ContractNegotiationRequest,
            ContractTerm,
        )
        from fastmcp.server.security.contracts.broker import ContextBroker

        broker = ContextBroker(server_id="my-server")

        request = ContractNegotiationRequest(
            agent_id="agent-1",
            proposed_terms=[
                ContractTerm(description="Read-only access", constraint={"read_only": True}),
            ],
        )
        response = await broker.negotiate(request)

    Args:
        server_id: Identity of this server.
        crypto_handler: Optional handler for contract signing.
        exchange_log: Optional log for non-repudiation recording.
        term_evaluator: Async callable that evaluates proposed terms.
            Receives (terms, context) and returns (accepted_terms, rejected_reasons).
            If None, all terms are accepted by default.
        default_terms: Server-mandated terms added to every contract.
        max_rounds: Maximum negotiation rounds before auto-reject.
        session_timeout: How long sessions remain active.
        contract_duration: Default contract validity duration.
    """

    def __init__(
        self,
        server_id: str = "securemcp-server",
        *,
        crypto_handler: ContractCryptoHandler | None = None,
        exchange_log: ExchangeLog | None = None,
        term_evaluator: Any = None,
        default_terms: list[ContractTerm] | None = None,
        max_rounds: int = 5,
        session_timeout: timedelta = timedelta(minutes=30),
        contract_duration: timedelta = timedelta(hours=1),
        broker_id: str = "default",
        backend: StorageBackend | None = None,
    ) -> None:
        self.server_id = server_id
        self.broker_id = broker_id
        self._backend = backend
        self.crypto_handler = crypto_handler
        self.exchange_log = exchange_log or ExchangeLog()
        self._term_evaluator = term_evaluator
        self.default_terms = default_terms or []
        self.max_rounds = max_rounds
        self.session_timeout = session_timeout
        self.contract_duration = contract_duration

        self._sessions: dict[str, NegotiationSession] = {}
        self._active_contracts: dict[str, Contract] = {}
        self._lock = asyncio.Lock()

        # Load persisted contracts
        if self._backend is not None:
            self._load_from_backend()

    def _load_from_backend(self) -> None:
        """Load active contracts from backend."""
        if self._backend is None:
            return
        from fastmcp.server.security.storage.serialization import contract_from_dict

        raw_contracts = self._backend.load_contracts(self.broker_id)
        for contract_id, data in raw_contracts.items():
            contract = contract_from_dict(data)
            self._active_contracts[contract_id] = contract

    async def negotiate(
        self, request: ContractNegotiationRequest
    ) -> ContractNegotiationResponse:
        """Process a negotiation request.

        If the request has an existing session_id, continues that session.
        Otherwise, starts a new session.

        Args:
            request: The negotiation request from an agent.

        Returns:
            ContractNegotiationResponse with the outcome.
        """
        async with self._lock:
            if request.session_id and request.session_id in self._sessions:
                return await self._continue_negotiation(request)
            else:
                return await self._start_negotiation(request)

    async def _start_negotiation(
        self, request: ContractNegotiationRequest
    ) -> ContractNegotiationResponse:
        """Start a new negotiation session."""
        session = NegotiationSession(
            agent_id=request.agent_id,
            server_id=self.server_id,
        )
        self._sessions[session.session_id] = session

        # Log session start
        self.exchange_log.record(
            session_id=session.session_id,
            event_type=ExchangeEventType.SESSION_STARTED,
            actor_id=self.server_id,
            data={"agent_id": request.agent_id},
        )

        # Log received proposal
        self.exchange_log.record(
            session_id=session.session_id,
            event_type=ExchangeEventType.PROPOSAL_RECEIVED,
            actor_id=request.agent_id,
            data={
                "term_count": len(request.proposed_terms),
                "request_id": request.request_id,
            },
        )

        # Merge agent terms with server default terms
        all_terms = list(self.default_terms) + list(request.proposed_terms)

        # Evaluate terms
        accepted_terms, rejected_reasons = await self._evaluate_terms(
            all_terms, request.context
        )

        if rejected_reasons:
            # Counter-propose with only accepted terms
            session.current_terms = accepted_terms
            session.round_count += 1
            session.status = NegotiationStatus.COUNTER

            self.exchange_log.record(
                session_id=session.session_id,
                event_type=ExchangeEventType.COUNTER_SENT,
                actor_id=self.server_id,
                data={"reasons": rejected_reasons},
            )

            return ContractNegotiationResponse(
                request_id=request.request_id,
                session_id=session.session_id,
                status=NegotiationStatus.COUNTER,
                counter_terms=accepted_terms,
                reason="; ".join(rejected_reasons),
            )

        # All terms accepted — create contract
        contract = await self._create_contract(session, accepted_terms)
        session.contract = contract
        session.status = NegotiationStatus.ACCEPTED

        self.exchange_log.record(
            session_id=session.session_id,
            event_type=ExchangeEventType.ACCEPTED,
            actor_id=self.server_id,
            data={"contract_id": contract.contract_id},
        )

        return ContractNegotiationResponse(
            request_id=request.request_id,
            session_id=session.session_id,
            status=NegotiationStatus.ACCEPTED,
            contract=contract,
        )

    async def _continue_negotiation(
        self, request: ContractNegotiationRequest
    ) -> ContractNegotiationResponse:
        """Continue an existing negotiation session."""
        session = self._sessions[request.session_id]

        # Check session timeout
        elapsed = datetime.now(timezone.utc) - session.last_activity
        if elapsed > self.session_timeout:
            session.status = NegotiationStatus.REJECTED
            return ContractNegotiationResponse(
                request_id=request.request_id,
                session_id=session.session_id,
                status=NegotiationStatus.REJECTED,
                reason="Negotiation session timed out",
            )

        # Check max rounds
        if session.round_count >= self.max_rounds:
            session.status = NegotiationStatus.REJECTED

            self.exchange_log.record(
                session_id=session.session_id,
                event_type=ExchangeEventType.REJECTED,
                actor_id=self.server_id,
                data={"reason": "max_rounds_exceeded"},
            )

            return ContractNegotiationResponse(
                request_id=request.request_id,
                session_id=session.session_id,
                status=NegotiationStatus.REJECTED,
                reason=f"Maximum negotiation rounds ({self.max_rounds}) exceeded",
            )

        session.last_activity = datetime.now(timezone.utc)
        session.round_count += 1

        # Log the counter-proposal received
        self.exchange_log.record(
            session_id=session.session_id,
            event_type=ExchangeEventType.COUNTER_RECEIVED,
            actor_id=request.agent_id,
            data={"round": session.round_count},
        )

        # Evaluate the new proposal
        accepted_terms, rejected_reasons = await self._evaluate_terms(
            request.proposed_terms, request.context
        )

        if rejected_reasons:
            session.current_terms = accepted_terms
            session.status = NegotiationStatus.COUNTER

            self.exchange_log.record(
                session_id=session.session_id,
                event_type=ExchangeEventType.COUNTER_SENT,
                actor_id=self.server_id,
                data={"reasons": rejected_reasons, "round": session.round_count},
            )

            return ContractNegotiationResponse(
                request_id=request.request_id,
                session_id=session.session_id,
                status=NegotiationStatus.COUNTER,
                counter_terms=accepted_terms,
                reason="; ".join(rejected_reasons),
            )

        # All accepted
        contract = await self._create_contract(session, accepted_terms)
        session.contract = contract
        session.status = NegotiationStatus.ACCEPTED

        self.exchange_log.record(
            session_id=session.session_id,
            event_type=ExchangeEventType.ACCEPTED,
            actor_id=self.server_id,
            data={"contract_id": contract.contract_id},
        )

        return ContractNegotiationResponse(
            request_id=request.request_id,
            session_id=session.session_id,
            status=NegotiationStatus.ACCEPTED,
            contract=contract,
        )

    async def _evaluate_terms(
        self,
        terms: list[ContractTerm],
        context: dict[str, Any],
    ) -> tuple[list[ContractTerm], list[str]]:
        """Evaluate proposed terms using the configured evaluator.

        Returns (accepted_terms, rejection_reasons).
        If no evaluator is configured, all terms are accepted.
        """
        if self._term_evaluator is None:
            return terms, []

        try:
            result = self._term_evaluator(terms, context)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception:
            logger.warning("Term evaluator failed", exc_info=True)
            return terms, []

    async def _create_contract(
        self,
        session: NegotiationSession,
        terms: list[ContractTerm],
    ) -> Contract:
        """Create and optionally sign a contract."""
        contract = Contract(
            session_id=session.session_id,
            server_id=self.server_id,
            agent_id=session.agent_id,
            terms=terms,
            status=ContractStatus.ACTIVE,
        )
        contract.set_default_expiry(self.contract_duration)

        # Sign if crypto handler available
        if self.crypto_handler is not None:
            contract_data = contract.to_dict()
            sig = self.crypto_handler.sign(contract_data, signer_id=self.server_id)
            contract.signatures[self.server_id] = sig.signature

            self.exchange_log.record(
                session_id=session.session_id,
                event_type=ExchangeEventType.CONTRACT_SIGNED,
                actor_id=self.server_id,
                data={
                    "contract_id": contract.contract_id,
                    "digest": compute_digest(contract_data),
                },
            )

        # Store as active contract
        self._active_contracts[contract.contract_id] = contract

        # Persist to backend
        if self._backend is not None:
            from fastmcp.server.security.storage.serialization import contract_to_dict

            self._backend.save_contract(
                self.broker_id, contract.contract_id, contract_to_dict(contract)
            )

        return contract

    def get_contract(self, contract_id: str) -> Contract | None:
        """Look up an active contract by ID."""
        return self._active_contracts.get(contract_id)

    def get_session(self, session_id: str) -> NegotiationSession | None:
        """Look up a negotiation session."""
        return self._sessions.get(session_id)

    def get_active_contracts_for_agent(self, agent_id: str) -> list[Contract]:
        """Get all active (valid) contracts for a specific agent."""
        return [
            c
            for c in self._active_contracts.values()
            if c.agent_id == agent_id and c.is_valid()
        ]

    async def revoke_contract(self, contract_id: str, reason: str = "") -> bool:
        """Revoke an active contract.

        Returns True if revoked, False if contract not found.
        """
        contract = self._active_contracts.get(contract_id)
        if contract is None:
            return False

        contract.status = ContractStatus.REVOKED

        self.exchange_log.record(
            session_id=contract.session_id,
            event_type=ExchangeEventType.CONTRACT_REVOKED,
            actor_id=self.server_id,
            data={"contract_id": contract_id, "reason": reason},
        )

        # Update persisted state
        if self._backend is not None:
            from fastmcp.server.security.storage.serialization import contract_to_dict

            self._backend.save_contract(
                self.broker_id, contract_id, contract_to_dict(contract)
            )

        return True

    @property
    def active_contract_count(self) -> int:
        """Number of currently valid contracts."""
        return sum(1 for c in self._active_contracts.values() if c.is_valid())

    @property
    def session_count(self) -> int:
        """Number of negotiation sessions."""
        return len(self._sessions)
