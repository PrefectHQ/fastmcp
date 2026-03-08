"""Tests for the exchange log."""

from __future__ import annotations

from fastmcp.server.security.contracts.exchange_log import (
    ExchangeEventType,
    ExchangeLog,
)


class TestExchangeLog:
    def test_record_entry(self):
        log = ExchangeLog()
        entry = log.record(
            session_id="s1",
            event_type=ExchangeEventType.SESSION_STARTED,
            actor_id="server-1",
            data={"agent_id": "a1"},
        )
        assert entry.session_id == "s1"
        assert entry.event_type == ExchangeEventType.SESSION_STARTED
        assert entry.actor_id == "server-1"
        assert entry.data["agent_id"] == "a1"
        assert entry.data_hash  # Non-empty
        assert entry.previous_hash == "genesis"

    def test_chain_linking(self):
        log = ExchangeLog()
        e1 = log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        e2 = log.record("s1", ExchangeEventType.PROPOSAL_RECEIVED, "agt")

        assert e1.previous_hash == "genesis"
        assert e2.previous_hash == e1.compute_hash()

    def test_separate_session_chains(self):
        log = ExchangeLog()
        log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        e2 = log.record("s2", ExchangeEventType.SESSION_STARTED, "srv")

        # s2's first entry should link to genesis, not s1's entry
        assert e2.previous_hash == "genesis"

    def test_verify_chain_valid(self):
        log = ExchangeLog()
        log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        log.record("s1", ExchangeEventType.PROPOSAL_RECEIVED, "agt")
        log.record("s1", ExchangeEventType.ACCEPTED, "srv")

        assert log.verify_chain("s1") is True

    def test_verify_empty_chain(self):
        log = ExchangeLog()
        assert log.verify_chain("nonexistent") is True

    def test_get_session_entries(self):
        log = ExchangeLog()
        log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        log.record("s2", ExchangeEventType.SESSION_STARTED, "srv")
        log.record("s1", ExchangeEventType.ACCEPTED, "srv")

        s1_entries = log.get_session_entries("s1")
        assert len(s1_entries) == 2
        assert s1_entries[0].event_type == ExchangeEventType.SESSION_STARTED
        assert s1_entries[1].event_type == ExchangeEventType.ACCEPTED

    def test_entry_count_and_session_count(self):
        log = ExchangeLog()
        log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        log.record("s2", ExchangeEventType.SESSION_STARTED, "srv")
        log.record("s1", ExchangeEventType.ACCEPTED, "srv")

        assert log.entry_count == 3
        assert log.session_count == 2

    def test_compute_hash_deterministic(self):
        log = ExchangeLog()
        entry = log.record("s1", ExchangeEventType.SESSION_STARTED, "srv")
        h1 = entry.compute_hash()
        h2 = entry.compute_hash()
        assert h1 == h2
