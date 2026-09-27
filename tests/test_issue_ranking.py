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
