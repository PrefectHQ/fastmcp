"""Tests for contract data models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp.server.security.contracts.schema import (
    Contract,
    ContractNegotiationRequest,
    ContractNegotiationResponse,
    ContractStatus,
    ContractTerm,
    NegotiationStatus,
    TermType,
)


class TestContractTerm:
    def test_default_term(self):
        term = ContractTerm()
        assert term.term_type == TermType.CUSTOM
        assert term.description == ""
        assert term.constraint == {}
        assert term.required is False

    def test_term_with_values(self):
        term = ContractTerm(
            term_type=TermType.DATA_USAGE,
            description="Read-only access",
            constraint={"read_only": True},
            required=True,
        )
        assert term.term_type == TermType.DATA_USAGE
        assert term.constraint["read_only"] is True
        assert term.required is True


class TestContract:
    def test_default_contract(self):
        contract = Contract()
        assert contract.status == ContractStatus.DRAFT
        assert contract.terms == []
        assert contract.signatures == {}
        assert contract.version == 1

    def test_contract_is_valid_when_active(self):
        contract = Contract(status=ContractStatus.ACTIVE)
        assert contract.is_valid()

    def test_contract_not_valid_when_draft(self):
        contract = Contract(status=ContractStatus.DRAFT)
        assert not contract.is_valid()

    def test_contract_not_valid_when_expired(self):
        contract = Contract(
            status=ContractStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert not contract.is_valid()

    def test_contract_valid_before_expiry(self):
        contract = Contract(
            status=ContractStatus.ACTIVE,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert contract.is_valid()

    def test_is_signed_by(self):
        contract = Contract(signatures={"server-1": "abc123"})
        assert contract.is_signed_by("server-1")
        assert not contract.is_signed_by("server-2")

    def test_set_default_expiry(self):
        contract = Contract()
        contract.set_default_expiry(timedelta(hours=2))
        assert contract.expires_at is not None
        expected = contract.created_at + timedelta(hours=2)
        assert abs((contract.expires_at - expected).total_seconds()) < 1

    def test_to_dict(self):
        term = ContractTerm(
            term_type=TermType.RATE_LIMIT,
            description="Max 100 calls/min",
            constraint={"max_calls": 100},
        )
        contract = Contract(
            server_id="srv",
            agent_id="agt",
            terms=[term],
            status=ContractStatus.ACTIVE,
        )
        d = contract.to_dict()
        assert d["server_id"] == "srv"
        assert d["agent_id"] == "agt"
        assert len(d["terms"]) == 1
        assert d["terms"][0]["term_type"] == "rate_limit"
        assert d["status"] == "active"


class TestNegotiationMessages:
    def test_request_defaults(self):
        req = ContractNegotiationRequest()
        assert req.agent_id == ""
        assert req.proposed_terms == []

    def test_response_defaults(self):
        resp = ContractNegotiationResponse()
        assert resp.status == NegotiationStatus.PENDING
        assert resp.contract is None
