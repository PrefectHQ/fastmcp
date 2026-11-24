from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

import flows.agentic_digest as digest
from flows.agentic_stack_plans import OUTPUTS, STANDALONE_ROLES, Digest


def test_digest_plan_reads_the_config_key():
    flow_id, version_id = uuid4(), uuid4()
    plan = digest.digest_plan(
        {"digest": {"flow_id": str(flow_id), "version_id": str(version_id)}}
    )
    assert plan is not None
    assert (plan.flow_id, plan.version_id) == (flow_id, version_id)
    assert digest.digest_plan({}) is None
    assert digest.digest_plan({"digest": "nope"}) is None
    assert digest.digest_plan(None) is None


def test_waiting_reads_the_triage_queue_and_drops_pull_requests():
    paths = []

    def pages(path):
        paths.append(path)
        return [{"number": 1}, {"number": 2, "pull_request": {}}]

    assert digest.waiting(pages) == [{"number": 1}]
    assert paths == [
        "repos/PrefectHQ/fastmcp/issues?state=open&labels=needs%3Atriage"
        "&sort=created&direction=asc"
    ]


def test_queue_entry_carries_the_recorded_next_step(monkeypatch):
    keys = []

    def artifacts(key, limit):
        keys.append((key, limit))
        return [{"line": "Review #6309 against the repro."}]

    monkeypatch.setattr(digest, "artifacts", artifacts)
    item = {
        "number": 6303,
        "title": "Tasks break on Valkey",
        "user": {"login": "reporter"},
        "labels": [{"name": "server"}, {"name": "needs:triage"}],
        "created_at": "2026-11-20T08:00:00Z",
        "comments": 4,
    }
    entry = digest.queue_entry(item, datetime(2026, 11, 23, 9, tzinfo=UTC))
    assert entry == {
        "number": 6303,
        "title": "Tasks break on Valkey",
        "author": "reporter",
        "labels": ["needs:triage", "server"],
        "age_days": 3,
        "comments": 4,
        "next_step": "Review #6309 against the repro.",
    }
    assert keys == [("fastmcp-triage-next-6303", 1)]


def test_queue_entry_without_a_recorded_step(monkeypatch):
    monkeypatch.setattr(digest, "artifacts", lambda key, limit: [])
    item = {"number": 7, "created_at": "2026-11-23T00:00:00Z", "user": None}
    entry = digest.queue_entry(item, datetime(2026, 11, 23, 9, tzinfo=UTC))
    assert entry["next_step"] is None
    assert entry["author"] is None
    assert entry["labels"] == []


def test_digest_is_a_standalone_role():
    assert OUTPUTS["digest"] is Digest
    assert "digest" in STANDALONE_ROLES
    with pytest.raises(ValidationError):
        Digest.model_validate({"post": True, "text": "x" * 3001, "why": "too long"})
    with pytest.raises(ValidationError):
        Digest.model_validate({"post": False, "text": "", "why": ""})
