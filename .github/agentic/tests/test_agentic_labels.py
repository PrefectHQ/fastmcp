from flows.agentic_labels import (
    DUPLICATE_LABEL,
    MRE_LABEL,
    TRIAGE_LABEL,
    apply_writes,
    mode,
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


def test_summary_names_mode() -> None:
    writes = [{"issue_number": 5, "label": TRIAGE_LABEL}]
    assert "Would apply `needs:triage` to #5" in summary(writes, "dry_run")
    assert "Applied" in summary(writes, "on")


def test_triage_never_starts_a_closing_clock_on_its_own() -> None:
    # #6630, #6692, #6730: each got needs MRE from a guess. Asking the reporter and
    # flagging a duplicate are now the maintainer's call; triage only queues them.
    review = {
        "bins": {
            "evidence": [evidence_item(1)],
            "pruning": [duplicate_item(2, 1)],
        }
    }
    writes = triage_labels(review, lambda n: issue(n))
    assert [(w["issue_number"], w["label"]) for w in writes] == [
        (1, TRIAGE_LABEL),
        (2, TRIAGE_LABEL),
    ]
    assert not any(w["label"] in {MRE_LABEL, DUPLICATE_LABEL} for w in writes)


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
        (4, TRIAGE_LABEL),
        (5, TRIAGE_LABEL),
        (6, TRIAGE_LABEL),
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


def test_assessor_must_answer_area_but_old_assessments_still_validate() -> None:
    from flows.agentic_stack_plans import Assessment

    schema = Assessment.model_json_schema()
    assert "area" in schema["required"]
    # A blank area satisfies "required", so the assessor is never offered one.
    assert "" not in schema["properties"]["area"]["enum"]
    assert "anyOf" not in schema["properties"]["area"]
    old = Assessment.model_validate(
        {
            "decision": "skip",
            "reason": "Retained before areas existed",
            "contract": "",
            "prior_art": [],
            "question": "",
        }
    )
    assert old.area == ""
