from __future__ import annotations

import pytest

from flows import agentic_stack_runtime
from flows.agentic_memory import (
    MemoryConfig,
    best_per_item,
    documents,
    memory_config,
    related,
    render,
    sync,
    windows,
)


def issue(number: int = 7, **extra) -> dict:
    return {
        "number": number,
        "title": "Client hangs on stdio exit",
        "state": "closed",
        "state_reason": "completed",
        "body": "Traceback: anyio.ClosedResourceError",
        "labels": [{"name": "bug"}],
        "user": {"login": "reporter", "type": "User"},
        "updated_at": "2026-11-20T00:00:00Z",
        "comments": 2,
        **extra,
    }


COMMENTS = [
    {
        "body": "same here on 4.2",
        "user": {"login": "someone", "type": "User"},
        "created_at": "2026-11-19T00:00:00Z",
    },
    {
        "body": "Found 1 possible duplicate issue",
        "user": {"login": "triage[bot]", "type": "Bot"},
        "created_at": "2026-11-19T01:00:00Z",
    },
]


def test_render_keeps_people_and_drops_bots():
    text = render(issue(), COMMENTS)
    assert text.startswith("#7: Client hangs on stdio exit")
    assert "State: closed (completed)" in text and "Labels: bug" in text
    assert "anyio.ClosedResourceError" in text
    assert "**someone** (2026-11-19): same here" in text
    assert "possible duplicate" not in text


def test_merged_pr_state_and_kind():
    pr = issue(pull_request={"merged_at": "2026-11-01T00:00:00Z"})
    doc = documents(pr, [])[0]
    assert doc["kind"] == "pr" and doc["state"] == "merged"
    assert doc["text"].startswith("fastmcp #7 (pr, merged): Client hangs")


def test_windows_overlap_and_cover_the_text():
    assert windows("short") == ["short"]
    text = " ".join(f"word{i}" for i in range(2000))
    parts = windows(text)
    assert len(parts) > 1
    assert all(len(part) <= 1000 for part in parts)
    assert parts[0][-100:] in parts[1]  # 200 characters of overlap
    assert parts[-1].endswith("word1999")


def test_documents_have_stable_ids_and_hash():
    first = documents(issue(body="x " * 2000), COMMENTS)
    again = documents(issue(body="x " * 2000), COMMENTS)
    assert [d["id"] for d in first] == [f"7:{i}" for i in range(len(first))]
    assert {d["content_hash"] for d in first} == {again[0]["content_hash"]}
    assert (
        documents(issue(body="changed"), COMMENTS)[0]["content_hash"]
        != first[0]["content_hash"]
    )


def test_best_per_item_keeps_first_chunk_of_each():
    rows = [{"number": 1}, {"number": 1}, {"number": 2}, {"number": 3}]
    assert [r["number"] for r in best_per_item(rows, 2)] == [1, 2]


def test_related_ranks_kinds_separately_and_excludes_itself():
    calls = []

    def search(text, kind, exclude, limit):
        calls.append((kind, exclude))
        base = 100 if kind == "issue" else 200
        return [
            {"number": base + n, "title": f"t{n}", "state": "open"} for n in (1, 1, 2)
        ]

    found = related(search, issue(), top_k=5)
    assert [c[0] for c in calls] == ["issue", "pr"]
    assert all(exclude == {7} for _, exclude in calls)
    assert [i["number"] for i in found["issues"]] == [101, 102]
    assert found["prs"][0]["url"] == "https://github.com/PrefectHQ/fastmcp/pull/201"
    assert "leads, not findings" in found["policy"]


def test_config_defaults_to_off():
    assert memory_config(None).mode == "off"
    assert memory_config({"memory": {"mode": "on"}}).mode == "on"
    assert memory_config({"memory": {"mode": "maybe"}}) == MemoryConfig()
    assert memory_config({"memory": {"top_k": 50}}) == MemoryConfig()


def test_sync_reads_comments_only_when_there_are_some():
    asked, written = [], []
    items = [issue(1), issue(2, comments=0), issue(3)]
    count = sync(
        items, lambda n: asked.append(n) or COMMENTS, written.append, batch_items=2
    )
    assert count == 3
    assert asked == [1, 3]
    assert [sorted({d["number"] for d in batch}) for batch in written] == [[1, 2], [3]]


@pytest.fixture
def config(monkeypatch):
    def use(value):
        monkeypatch.setattr(
            agentic_stack_runtime.Variable, "get", staticmethod(lambda *a, **k: value)
        )

    return use


def test_assessment_is_unchanged_while_memory_is_off(config):
    config({})
    assert agentic_stack_runtime.with_related({"evidence": {}}, issue()) == {
        "evidence": {}
    }


def test_memory_failure_never_stops_triage(config, monkeypatch):
    config({"memory": {"mode": "on"}})

    def broken(name):
        raise RuntimeError("no such block")

    monkeypatch.setattr(agentic_stack_runtime, "secret_sync", broken)
    projected = agentic_stack_runtime.with_related({"evidence": {}}, issue())
    assert projected["evidence"] == {}
    assert projected["related"] == {"unavailable": "RuntimeError: no such block"}
