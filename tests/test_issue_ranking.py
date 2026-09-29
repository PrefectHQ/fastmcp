import importlib.util
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def ranking_module():
    path = Path(__file__).parents[1] / "scripts" / "rank_issues.py"
    spec = importlib.util.spec_from_file_location("rank_issues_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def issue(module, number=1):
    now = datetime.now(timezone.utc)
    return module.Issue(
        number=number,
        title="Tool execution fails",
        body="Reproduction",
        url=f"https://github.com/PrefectHQ/fastmcp/issues/{number}",
        created_at=now - timedelta(days=3),
        updated_at=now,
        author="reporter",
        author_type="User",
        author_association="NONE",
        author_created_at=now,
        author_followers=0,
        labels=[],
        assignees=[],
        reactions=0,
        comment_count=0,
        commenters=[],
        maintainer_replied_at=None,
        linked_prs=[],
    )


def test_public_export_removes_private_influence_before_sorting(ranking_module):
    m = ranking_module
    first = m.Ranked(
        issue(m, 1),
        100,
        {"severity": 0.1, "author_trust": 100},
        {"author": "PRIVATE"},
        None,
    )
    second = m.Ranked(
        issue(m, 2), -100, {"severity": 1, "low_effort": 100, "spray": 100}, {}, None
    )
    doc = m.public_snapshot(
        m.RankedPage([first, second], "next-page"),
        m.Config(repo="PrefectHQ/fastmcp", judge="none"),
    )
    assert [r["number"] for r in doc["items"]] == [2, 1]
    assert [r["score"] for r in doc["items"]] == [3, 0.3]
    assert doc["has_more"] is True
    assert doc["examined"] == 2
    assert doc["judged"] == 0
    serialized = json.dumps(doc)
    for private in (
        "PRIVATE",
        "author_trust",
        "low_effort",
        "spray",
        '"facts"',
        '"judgment"',
    ):
        assert private not in serialized


def test_public_scoring_is_independent_of_author_reputation(ranking_module):
    m = ranking_module
    original = issue(m)
    famous = replace(
        original,
        author_followers=100000,
        author_created_at=original.created_at - timedelta(days=3650),
    )
    config = m.Config(repo="PrefectHQ/fastmcp", judge="none", public=True)
    now = datetime.now(timezone.utc)
    a = m.score_issue(original, None, None, [], config, now)
    b = m.score_issue(famous, None, None, [], config, now)
    assert a.score == b.score
    assert a.components == b.components
    assert "author_trust" not in a.components


def test_assignment_reduces_attention_and_triage_label_removes_neglect(ranking_module):
    m = ranking_module
    config = m.Config(repo="PrefectHQ/fastmcp", judge="none", public=True)
    now = datetime.now(timezone.utc)
    original = issue(m)
    unanswered = m.score_issue(original, None, None, [], config, now)
    assigned = m.score_issue(
        replace(original, assignees=["maintainer"]), None, None, [], config, now
    )
    waiting = m.score_issue(
        replace(original, labels=["needs MRE"]), None, None, [], config, now
    )
    assert unanswered.score > assigned.score > waiting.score
    assert waiting.components["neglect"] == 0


def test_partial_comment_history_does_not_imply_unanswered(ranking_module):
    m = ranking_module
    truncated = replace(issue(m), comment_count=80, commenters=["reporter"] * 50)
    ranked = m.score_issue(
        truncated,
        None,
        None,
        [],
        m.Config(repo="PrefectHQ/fastmcp", public=True),
        datetime.now(timezone.utc),
    )
    assert ranked.components["neglect"] == 0
    assert ranked.facts["maintainer_replied"] is None
    doc = m.public_snapshot(
        m.RankedPage([ranked], None), m.Config(repo="PrefectHQ/fastmcp")
    )
    assert doc["items"][0]["maintainer_replied"] is None


def test_public_kind_is_a_category_not_formatted_confidence(ranking_module):
    m = ranking_module
    judgment = m.Judgment(
        kind={"bug": 0.8, "question": 0.2},
        repro=1,
        actionable=1,
        severity=0.5,
        security=0,
        spec=0,
        downstream=0,
        low_effort=0,
    )
    ranked = m.Ranked(issue(m), 1, {"severity": 0.5}, {}, judgment)
    doc = m.public_snapshot(
        m.RankedPage([ranked], None), m.Config(repo="PrefectHQ/fastmcp", judge="jev")
    )
    assert doc["items"][0]["kind"] == "bug"
    assert doc["judged"] == 1


@pytest.mark.parametrize("fails", [False, True])
def test_cold_cache_budget_limits_attempts_even_when_calls_fail(
    ranking_module, tmp_path, fails
):
    import asyncio

    m = ranking_module

    class MeteredJudge:
        def __init__(self):
            self.calls = 0

        async def judge(self, state):
            self.calls += 1
            await asyncio.sleep(0)
            if fails:
                raise RuntimeError("provider unavailable")
            return m.Judgment(
                kind={"bug": 1},
                repro=1,
                actionable=1,
                severity=1,
                security=0,
                spec=0,
                downstream=0,
                low_effort=0,
            )

    judge = MeteredJudge()
    config = m.Config(
        repo="PrefectHQ/fastmcp", public=True, max_judgments=2, cache_dir=tmp_path
    )
    results = asyncio.run(
        m.judge_all(
            judge, [issue(m, n) for n in range(20)], config, m.Cache(tmp_path, True)
        )
    )
    assert judge.calls == 2
    assert len(results) == (0 if fails else 2)


def test_previous_public_assessment_reused_without_credentials(
    ranking_module, tmp_path
):
    import asyncio

    m = ranking_module
    config = m.Config(
        repo="PrefectHQ/fastmcp",
        public=True,
        max_judgments=0,
        previous=tmp_path / "previous.json",
    )
    original = issue(m)
    judgment = m.Judgment(
        kind={"bug": 1},
        repro=1,
        actionable=1,
        severity=1,
        security=0,
        spec=0,
        downstream=0,
        low_effort=0.99,
    )
    ranked = m.score_issue(
        original, None, judgment, [], config, datetime.now(timezone.utc)
    )
    snapshot = m.public_snapshot(m.RankedPage([ranked], None), config)
    assert "low_effort" not in snapshot["items"][0]["assessment"]
    config.previous.write_text(json.dumps({"attention": snapshot}))
    metadata_changed = replace(
        original,
        updated_at=original.updated_at + timedelta(hours=1),
        comment_count=1,
        commenters=["someone"],
    )
    results = asyncio.run(
        m.judge_all(None, [metadata_changed], config, m.Cache(tmp_path / "empty", True))
    )
    assert results[original.number].severity == 1
    assert results[original.number].low_effort == 0
    changed = replace(original, body="Different reproduction")
    assert (
        asyncio.run(
            m.judge_all(None, [changed], config, m.Cache(tmp_path / "empty", True))
        )
        == {}
    )
    new_model = replace(config, jev_model="different-model")
    assert (
        asyncio.run(
            m.judge_all(None, [original], new_model, m.Cache(tmp_path / "empty", True))
        )
        == {}
    )


def test_cache_keys_are_portable_and_keep_boundaries(ranking_module, tmp_path):
    cache = ranking_module.Cache(tmp_path, enabled=True)
    keys = [
        ("judgments", "PrefectHQ/fastmcp", "jev:jev-latest", "1"),
        ("judgments", "PrefectHQ", "fastmcp/jev:jev-latest", "1"),
    ]
    for index, key in enumerate(keys):
        cache.put({"index": index}, *key)
    for index, key in enumerate(keys):
        assert cache.get(*key) == {"index": index}
    assert cache.get("missing") is None
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 2
    for file in files:
        assert file.parent == tmp_path
        assert not set(file.name) & set('<>:"/\\|?*')
