from flows.agentic_labels import (
    DUPLICATE_LABEL,
    MRE_LABEL,
    TRIAGE_LABEL,
    apply_writes,
    mode,
    planned_writes,
    summary,
    triage_labels,
)


def issue(number: int, labels: tuple[str, ...] = (), **extra) -> dict:
    return {
        "number": number,
        "state": "open",
        "user": {"type": "User", "login": "reporter"},
        "labels": [{"name": name} for name in labels],
        **extra,
    }


def evidence_item(number: int, decision: str = "evidence_needed", review=True) -> dict:
    item = {"issue_number": number, "decision": decision}
    if review:
        item["adversarial_review"] = {"decision": "repair_evidence"}
    return item


def duplicate_item(number: int, canonical: int, comment: str = "") -> dict:
    return {
        "issue_number": number,
        "adversarial_review": {
            "recommendations": [
                {
                    "action": "close_duplicate",
                    "target_number": number,
                    "canonical_issue": canonical,
                    "public_comment": comment,
                }
            ]
        },
    }


def test_mode_defaults_to_dry_run() -> None:
    assert mode(None) == "dry_run"
    assert mode({}) == "dry_run"
    assert mode({"github_writes": "sure"}) == "dry_run"
    assert mode({"github_writes": "on"}) == "on"
    assert mode({"github_writes": "off"}) == "off"
    per_feature = {"github_writes": {"labels": "on", "sweep": "off"}}
    assert mode(per_feature) == "on"
    assert mode(per_feature, "sweep") == "off"
    assert mode({"github_writes": {"labels": "on"}}, "sweep") == "dry_run"


def test_reviewed_mre_outcome_gets_label_and_request() -> None:
    review = {"bins": {"evidence": [evidence_item(1)]}}
    writes = planned_writes(review, lambda n: issue(n))
    assert [(w["issue_number"], w["label"]) for w in writes] == [(1, MRE_LABEL)]
    assert "7 days" in writes[0]["comment"]


def test_mre_skips_unreviewed_reproduced_closed_labeled_prs_and_bots() -> None:
    live = {
        1: issue(1),
        2: issue(2),
        3: issue(3, state="closed"),
        4: issue(4, labels=(MRE_LABEL,)),
        5: issue(5, pull_request={}),
        6: issue(6, user={"type": "Bot"}),
        7: None,
        8: issue(8),
    }
    review = {
        "bins": {
            "evidence": [
                evidence_item(1, review=False),  # policy outcome: label exists already
                evidence_item(2, decision="reproduced"),
                evidence_item(3),
                evidence_item(4),
                evidence_item(5),
                evidence_item(6),
                evidence_item(7),
                evidence_item(8, decision="not_reproduced"),
            ]
        }
    }
    assert planned_writes(review, live.get) == []


def test_reviewed_duplicate_gets_closer_compatible_comment() -> None:
    review = {"bins": {"pruning": [duplicate_item(10, 3, "Same TypeError as #3.")]}}
    writes = planned_writes(review, lambda n: issue(n))
    assert len(writes) == 1
    write = writes[0]
    assert write["label"] == DUPLICATE_LABEL
    assert write["canonical_issue"] == 3
    # auto_close_duplicates.py finds the marking comment by this phrase.
    assert "possible duplicate issue" in write["comment"].lower()
    assert "#3" in write["comment"] and "Same TypeError" in write["comment"]


def test_duplicate_skips_self_prs_missing_canonical_and_other_actions() -> None:
    other = duplicate_item(12, 3)
    other["adversarial_review"]["recommendations"][0]["action"] = "close_settled"
    review = {
        "bins": {
            "pruning": [
                duplicate_item(10, 10),
                duplicate_item(11, 99),
                other,
                duplicate_item(13, 4),
                {"issue_number": 14},
            ]
        }
    }
    live = {
        10: issue(10),
        11: issue(11),
        12: issue(12),
        13: issue(13),
        4: issue(4, pull_request={}),
    }
    assert planned_writes(review, live.get) == []


def test_one_write_per_issue() -> None:
    review = {
        "bins": {"evidence": [evidence_item(1)], "pruning": [duplicate_item(1, 2)]}
    }
    writes = planned_writes(review, lambda n: issue(n))
    assert [w["label"] for w in writes] == [MRE_LABEL]


def test_apply_comments_before_labeling() -> None:
    calls = []
    writes = [{"issue_number": 5, "label": MRE_LABEL, "comment": "hi"}]
    apply_writes(writes, lambda path, body: calls.append((path, body)))
    assert calls == [
        ("repos/PrefectHQ/fastmcp/issues/5/comments", {"body": "hi"}),
        ("repos/PrefectHQ/fastmcp/issues/5/labels", {"labels": [MRE_LABEL]}),
    ]


def test_summary_names_mode() -> None:
    writes = [
        {
            "issue_number": 5,
            "label": DUPLICATE_LABEL,
            "comment": "",
            "canonical_issue": 2,
        }
    ]
    assert "Would apply `potential-duplicate` to #5 (duplicate of #2)" in summary(
        writes, "dry_run"
    )
    assert "Applied" in summary(writes, "on")


def test_assessed_issues_get_area_and_maintainer_queue_labels() -> None:
    review = {
        "bins": {
            "existing_review": [{"issue_number": 1, "assessment": {"area": "client"}}],
            "decisions": [{"issue_number": 2, "area": "auth"}],
            "evidence": [
                {"issue_number": 3, "decision": "reproduced", "assessment": {}},
                {"issue_number": 4, "decision": "evidence_needed", "area": "cli"},
            ],
            "pruning": [{"issue_number": 5}, duplicate_item(6, 1)],
            "unchanged": [{"issue_number": 7, "area": "server"}],
            "operational": [{"issue_number": 8, "assessment": {"area": "http"}}],
        }
    }
    writes = triage_labels(review, lambda n: issue(n))
    assert [(w["issue_number"], w["label"]) for w in writes] == [
        (1, "client"),
        (1, TRIAGE_LABEL),
        (2, "auth"),
        (2, TRIAGE_LABEL),
        (3, TRIAGE_LABEL),
        (4, "cli"),
        (5, TRIAGE_LABEL),
        (7, "server"),
        (8, "http"),
    ]
    assert not any(w.get("comment") for w in writes)


def test_triage_labels_respect_existing_labels_and_unwritable_items() -> None:
    live = {
        1: issue(1, labels=("server",)),
        2: issue(2, labels=(TRIAGE_LABEL,)),
        3: issue(3, labels=(MRE_LABEL,)),
        4: issue(4, state="closed"),
        5: issue(5, pull_request={}),
        6: issue(6, user={"type": "Bot"}),
    }
    review = {
        "bins": {
            "decisions": [{"issue_number": n, "area": "client"} for n in range(1, 7)]
            + [{"issue_number": 9, "area": "not-an-area"}],
        }
    }
    writes = triage_labels(review, live.get)
    assert [(w["issue_number"], w["label"]) for w in writes] == [
        (1, TRIAGE_LABEL),
        (2, "client"),
        (3, "client"),
    ]


def test_label_only_writes_skip_the_comment() -> None:
    calls = []
    apply_writes(
        [{"issue_number": 5, "label": "client"}],
        lambda path, body: calls.append((path, body)),
    )
    assert calls == [
        ("repos/PrefectHQ/fastmcp/issues/5/labels", {"labels": ["client"]}),
    ]
