"""Tests for the Context Broker."""

from __future__ import annotations

import pytest

from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    SigningAlgorithm,
)
from fastmcp.server.security.contracts.exchange_log import ExchangeLog
from fastmcp.server.security.contracts.schema import (
    ContractNegotiationRequest,
    ContractStatus,
    ContractTerm,
    NegotiationStatus,
    TermType,
)


class TestContextBrokerBasics:
    @pytest.mark.anyio
    async def test_simple_negotiation_accept(self):
        """All terms accepted by default (no evaluator)."""
        broker = ContextBroker(server_id="test-server")
        request = ContractNegotiationRequest(
            agent_id="agent-1",
            proposed_terms=[
                ContractTerm(description="Read access", constraint={"read_only": True}),
            ],
        )
        response = await broker.negotiate(request)

        assert response.status == NegotiationStatus.ACCEPTED
        assert response.contract is not None
        assert response.contract.status == ContractStatus.ACTIVE
        assert response.contract.agent_id == "agent-1"
        assert response.contract.server_id == "test-server"
        assert len(response.contract.terms) == 1

    @pytest.mark.anyio
    async def test_negotiation_with_default_terms(self):
        """Server default terms are merged in."""
        default_term = ContractTerm(
            term_type=TermType.AUDIT,
            description="Audit logging required",
            required=True,
        )
        broker = ContextBroker(
            server_id="srv",
            default_terms=[default_term],
        )
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[
                ContractTerm(description="Agent term"),
            ],
        )
        response = await broker.negotiate(request)

        assert response.status == NegotiationStatus.ACCEPTED
        assert len(response.contract.terms) == 2  # default + agent

    @pytest.mark.anyio
    async def test_negotiation_with_evaluator_reject(self):
        """Term evaluator can reject terms and counter-propose."""

        def evaluator(terms, context):
            accepted = [t for t in terms if t.required]
            rejected = [f"Term '{t.term_id}' rejected" for t in terms if not t.required]
            return accepted, rejected

        broker = ContextBroker(
            server_id="srv",
            term_evaluator=evaluator,
        )
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[
                ContractTerm(description="Optional term", required=False),
            ],
        )
        response = await broker.negotiate(request)

        assert response.status == NegotiationStatus.COUNTER
        assert response.reason  # Contains rejection reasons

    @pytest.mark.anyio
    async def test_negotiation_with_async_evaluator(self):
        """Async term evaluators are supported."""

        async def evaluator(terms, context):
            return terms, []

        broker = ContextBroker(server_id="srv", term_evaluator=evaluator)
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm(description="Term")],
        )
        response = await broker.negotiate(request)
        assert response.status == NegotiationStatus.ACCEPTED

    @pytest.mark.anyio
    async def test_contract_expiry_set(self):
        broker = ContextBroker(server_id="srv")
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        response = await broker.negotiate(request)
        contract = response.contract
        assert contract.expires_at is not None


class TestContextBrokerSessions:
    @pytest.mark.anyio
    async def test_continue_session(self):
        """Continue an existing negotiation session."""

        call_count = 0

        def evaluator(terms, context):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [], ["Rejected first time"]
            return terms, []

        broker = ContextBroker(server_id="srv", term_evaluator=evaluator)

        # First round: counter-propose
        req1 = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm(description="V1")],
        )
        resp1 = await broker.negotiate(req1)
        assert resp1.status == NegotiationStatus.COUNTER
        session_id = resp1.session_id

        # Second round: accept
        req2 = ContractNegotiationRequest(
            session_id=session_id,
            agent_id="agt",
            proposed_terms=[ContractTerm(description="V2")],
        )
        resp2 = await broker.negotiate(req2)
        assert resp2.status == NegotiationStatus.ACCEPTED

    @pytest.mark.anyio
    async def test_max_rounds_exceeded(self):
        """Session rejected after max rounds."""

        def always_reject(terms, context):
            return [], ["Nope"]

        broker = ContextBroker(
            server_id="srv",
            term_evaluator=always_reject,
            max_rounds=2,
        )

        # Round 1
        req = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        resp = await broker.negotiate(req)
        session_id = resp.session_id

        # Round 2
        req2 = ContractNegotiationRequest(
            session_id=session_id,
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        resp2 = await broker.negotiate(req2)

        # Round 3 — should be rejected
        req3 = ContractNegotiationRequest(
            session_id=session_id,
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        resp3 = await broker.negotiate(req3)
        assert resp3.status == NegotiationStatus.REJECTED
        assert "exceeded" in resp3.reason.lower()


class TestContextBrokerCrypto:
    @pytest.mark.anyio
    async def test_contract_signed_with_crypto(self):
        handler = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"test-key",
        )
        broker = ContextBroker(
            server_id="srv",
            crypto_handler=handler,
        )
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        response = await broker.negotiate(request)

        contract = response.contract
        assert contract.is_signed_by("srv")

    @pytest.mark.anyio
    async def test_exchange_log_records_events(self):
        log = ExchangeLog()
        broker = ContextBroker(
            server_id="srv",
            exchange_log=log,
        )
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        await broker.negotiate(request)

        # Should have: session_started, proposal_received, accepted
        assert log.entry_count >= 3


class TestContextBrokerContractManagement:
    @pytest.mark.anyio
    async def test_get_contract(self):
        broker = ContextBroker(server_id="srv")
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        response = await broker.negotiate(request)

        contract = broker.get_contract(response.contract.contract_id)
        assert contract is not None
        assert contract.contract_id == response.contract.contract_id

    @pytest.mark.anyio
    async def test_get_active_contracts_for_agent(self):
        broker = ContextBroker(server_id="srv")

        # Create two contracts for same agent
        for _ in range(2):
            req = ContractNegotiationRequest(
                agent_id="agt",
                proposed_terms=[ContractTerm()],
            )
            await broker.negotiate(req)

        contracts = broker.get_active_contracts_for_agent("agt")
        assert len(contracts) == 2

    @pytest.mark.anyio
    async def test_revoke_contract(self):
        broker = ContextBroker(server_id="srv")
        request = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        response = await broker.negotiate(request)

        result = await broker.revoke_contract(
            response.contract.contract_id, reason="Testing"
        )
        assert result is True

        contract = broker.get_contract(response.contract.contract_id)
        assert contract.status == ContractStatus.REVOKED

    @pytest.mark.anyio
    async def test_revoke_nonexistent_contract(self):
        broker = ContextBroker(server_id="srv")
        result = await broker.revoke_contract("nonexistent")
        assert result is False

    @pytest.mark.anyio
    async def test_active_contract_count(self):
        broker = ContextBroker(server_id="srv")
        assert broker.active_contract_count == 0

        req = ContractNegotiationRequest(
            agent_id="agt",
            proposed_terms=[ContractTerm()],
        )
        await broker.negotiate(req)
        assert broker.active_contract_count == 1
