import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.client import RemoteDisconnected
from uuid import uuid4

import pytest
from prefect.testing.utilities import prefect_test_harness

from flows import agentic_review_delivery as delivery
from flows.agentic_artifacts import published_reviews
from flows.agentic_claims import claim_work
from flows.agentic_freshness import revalidate
from flows.agentic_github import GitHub
from flows.agentic_review_feedback import attach_feedback, read_feedback
from flows.agentic_review_publication import format_review, post_review
from flows.agentic_stack_plans import ReviewFeedback
from flows.agentic_stack_policy import Batch
from flows.agentic_stack_runtime import Runtime
from tests.test_agentic_review_publication import BASE, BOT, HEAD


def feedback_body(
    head=HEAD,
    base=BASE,
    *,
    outcome="no_findings",
    findings=(),
    summary="No actionable defects found.",
):
    feedback = ReviewFeedback(
        outcome=outcome,
        finding_ids=[{"id": name, "severity": severity} for name, severity in findings],
    )
    return attach_feedback(
        format_review(7, head, base, summary, "Focused checks completed."), feedback
    )


@pytest.mark.parametrize("changed", ["head", "base", "both", "wording"])
def test_completed_equivalent_review_does_not_repost(endpoint, changed):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    first = post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    head = "c" * 40 if changed in {"head", "both"} else HEAD
    base = "d" * 40 if changed in {"base", "both"} else BASE
    state["pr"]["head"]["sha"] = head
    guards = []
    result = post_review(
        client,
        7,
        head,
        base,
        BOT,
        feedback_body(head, base, summary="The focused review found no defects."),
        lambda: guards.append(True),
    )
    assert result["status"] == "unchanged_feedback"
    assert result["verified_head_sha"] == head
    assert result["verified_baseline_sha"] == base
    assert result["previous_review"]["review_id"] == first["review_id"]
    assert result["previous_review"]["head_sha"] == HEAD
    assert "review_id" not in result and "head_sha" not in result
    assert len(state["reviews"]) == 1
    assert guards == [True]


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ((), (), "unchanged_feedback"),
        (
            (("defaults-dropped", "blocking"),),
            (("defaults-dropped", "blocking"),),
            "unchanged_feedback",
        ),
        ((), (("defaults-dropped", "blocking"),), "posted"),
        ((("defaults-dropped", "blocking"),), (), "posted"),
        (
            (("defaults-dropped", "blocking"),),
            (("defaults-dropped", "non_blocking"),),
            "posted",
        ),
        (
            (("defaults-dropped", "blocking"),),
            (("defaults-dropped-v2", "blocking"),),
            "posted",
        ),
        (
            (("defaults-dropped", "blocking"), ("cache-leak", "blocking")),
            (("defaults-dropped", "blocking"),),
            "posted",
        ),
    ],
)
def test_meaningful_findings_changes_are_public_even_at_same_revision(
    endpoint, before, after, expected
):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    for findings, summary in (
        (before, "Previous assessment."),
        (after, "Fresh assessment."),
    ):
        result = post_review(
            client,
            7,
            HEAD,
            BASE,
            BOT,
            feedback_body(
                outcome="findings" if findings else "no_findings",
                findings=findings,
                summary=summary,
            ),
            lambda: None,
        )
    assert result["status"] == expected
    assert len(state["reviews"]) == (1 if expected == "unchanged_feedback" else 2)


@pytest.mark.parametrize(
    "change", ["legacy", "edit", "dismissed", "identity", "inconclusive", "malformed"]
)
def test_only_intact_latest_bot_feedback_can_suppress(endpoint, change):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    previous = state["reviews"][0]
    if change == "legacy":
        previous["body"] = format_review(7, HEAD, BASE, "No defects.", "Checked.")
    elif change == "edit":
        previous["body"] += "\nHuman correction."
    elif change == "dismissed":
        previous["state"] = "DISMISSED"
    elif change == "identity":
        previous["user"]["type"] = "User"
    elif change == "inconclusive":
        previous["body"] = feedback_body(outcome="inconclusive")
    elif change == "malformed":
        previous["body"] = previous["body"].replace(
            '"outcome": "no_findings"', '"outcome": null'
        )
    head = "c" * 40
    state["pr"]["head"]["sha"] = head
    result = post_review(client, 7, head, BASE, BOT, feedback_body(head), lambda: None)
    assert result["status"] == "posted"
    assert len(state["reviews"]) == 2


def test_latest_changed_findings_prevent_matching_an_older_review(endpoint):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    post_review(
        client,
        7,
        HEAD,
        BASE,
        BOT,
        feedback_body(outcome="findings", findings=(("defaults-dropped", "blocking"),)),
        lambda: None,
    )
    result = post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    assert result["status"] == "posted"
    assert len(state["reviews"]) == 3


@pytest.mark.parametrize("change", ["discussion", "head", "hold"])
def test_suppression_still_requires_fresh_evidence(endpoint, change):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    base = "c" * 40

    def guard():
        if change == "discussion":
            raise ValueError("New human evidence")
        if change == "head":
            state["pr"]["head"]["sha"] = "d" * 40
        else:
            state["pr"]["draft"] = True

    with pytest.raises(ValueError):
        post_review(client, 7, HEAD, base, BOT, feedback_body(base=base), guard)
    assert len(state["reviews"]) == 1


def test_inconclusive_feedback_is_never_semantically_suppressed(endpoint):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(
        client, 7, HEAD, BASE, BOT, feedback_body(outcome="inconclusive"), lambda: None
    )
    base = "c" * 40
    result = post_review(
        client,
        7,
        HEAD,
        base,
        BOT,
        feedback_body(base=base, outcome="inconclusive"),
        lambda: None,
    )
    assert result["status"] == "posted"
    assert len(state["reviews"]) == 2


def test_concurrent_baselines_share_existing_pr_claim(endpoint):
    state, url, _ = endpoint
    start = threading.Barrier(2)
    with prefect_test_harness():

        def publish(base):
            start.wait(timeout=10)
            with claim_work(
                ["fastmcp-pr-7-review"],
                uuid4(),
                datetime.now(UTC) + timedelta(seconds=30),
            ):
                return post_review(
                    GitHub("test-installation", url),
                    7,
                    HEAD,
                    base,
                    BOT,
                    feedback_body(base=base),
                    lambda: None,
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, publish, base)
                for base in (BASE, "c" * 40)
            ]
            results = [future.result(timeout=40) for future in futures]
    assert {r["status"] for r in results} == {"posted", "unchanged_feedback"}
    assert len(state["reviews"]) == 1


def test_lost_structured_post_is_reconciled_and_crlf_is_accepted(endpoint):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    state["disconnect"] = True
    with pytest.raises(RemoteDisconnected):
        post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    state["reviews"][0]["body"] = state["reviews"][0]["body"].replace("\n", "\r\n")
    result = post_review(
        client,
        7,
        HEAD,
        BASE,
        BOT,
        feedback_body(),
        lambda: pytest.fail("Reconciled POST"),
    )
    assert result["status"] == "already_posted"
    base = "c" * 40
    result = post_review(
        client, 7, HEAD, base, BOT, feedback_body(base=base), lambda: None
    )
    assert result["status"] == "unchanged_feedback"
    assert len(state["reviews"]) == 1


def test_suppression_retains_fresh_verification_without_relabeling_old_review(
    endpoint, evidence, monkeypatch
):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    previous = post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    head = "c" * 40
    base = "d" * 40
    state["pr"]["head"]["sha"] = head
    original = {
        **evidence,
        "stacks": [],
        "base_sha": base,
        "prs": [
            {
                "number": 7,
                "state": "open",
                "head_sha": head,
                "reviews": deepcopy(state["reviews"]),
                "timeline": [],
            }
        ],
    }
    public = {
        "pr_number": 7,
        "outcome": "no_findings",
        "summary": "No actionable defects found.",
        "requested_change": "",
        "evidence": "Fresh focused checks completed.",
    }
    review = {"decision": "review_existing", "public_reviews": [public]}
    check = {
        "pr_number": 7,
        "status": "verification_recorded",
        "head_sha": head,
        "baseline_sha": base,
    }
    outcome = {
        "issue_number": 2692,
        "adversarial_review": review,
        "triage_review_run_id": "independent",
        "verification": [check],
    }
    records = {
        "dedupe-issue-2692-triage-review": {"run_id": "independent", "result": review}
    }
    reads = []
    monkeypatch.setattr(delivery, "saved", lambda owner, key: records.get(key))
    monkeypatch.setattr(
        delivery, "record", lambda owner, key, value: records.setdefault(key, value)
    )
    monkeypatch.setattr(delivery, "owner_alive", lambda *_: None)
    monkeypatch.setattr(delivery, "secret_sync", lambda *_: "dummy")
    monkeypatch.setattr(delivery, "review_client", lambda *_: nullcontext(client))
    monkeypatch.setattr(delivery, "claim_work", lambda *_: nullcontext())

    def collect(*_):
        reads.append(True)
        return original

    monkeypatch.setattr(delivery, "collect", collect)
    now = datetime.now(UTC)
    batch = Batch(
        id="dedupe",
        started_at=now,
        expires_at=now + timedelta(minutes=5),
        groups=[
            {
                "name": "dedupe",
                "rationale": "Check fresh contribution",
                "base_sha": base,
                "branch": "main",
                "items": [{"issue_number": 2692}],
            }
        ],
    )
    runtime = Runtime(
        plans={
            role: {"flow_id": uuid4(), "version_id": uuid4()}
            for role in ("assess", "reproduce", "triage_review")
        },
        source_sha=base,
        publish_reviews=True,
        reviewer_app={"app_id": 42, "slug": "reviewer", "private_key_block": "dummy"},
    )
    owner = uuid4()
    for _ in range(2):
        result = delivery.deliver_reviews.fn(
            owner, batch, batch.groups[0], runtime, outcome, original
        )
        assert not result["posted"]
        suppressed = result["skipped"][0]
        assert suppressed["status"] == "unchanged_feedback"
        assert suppressed["verified_head_sha"] == head
        assert suppressed["previous_review"]["head_sha"] == previous["head_sha"]
    assert len(reads) == 2  # A retained suppression never bypasses freshness on retry.
    bins = {
        "existing_review": [
            {
                **outcome,
                "review_delivery": result,
                "published_reviews": result["posted"],
            }
        ],
        "operational": [],
    }
    retained = {"bins": bins}
    assert published_reviews(retained) == []
    assert (
        revalidate(retained, {2692: original}, {2692: "main"}, lambda *_: original)
        == retained
    )
    changed = deepcopy(original)
    changed["prs"][0]["reviews"][0]["body"] += "\nHuman correction."
    stale = revalidate(retained, {2692: original}, {2692: "main"}, lambda *_: changed)
    assert stale["bins"]["operational"][0]["status"] == "stale"
    assert stale["bins"]["operational"][0]["retained_outcomes"][0]["result"][
        "verification"
    ] == [check]
    assert len(state["reviews"]) == 1


def test_newest_review_with_edited_away_marker_cannot_fall_back_to_older_feedback(
    endpoint,
):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(client, 7, HEAD, BASE, BOT, feedback_body(), lambda: None)
    post_review(
        client,
        7,
        HEAD,
        BASE,
        BOT,
        feedback_body(outcome="findings", findings=(("defaults-dropped", "blocking"),)),
        lambda: None,
    )
    state["reviews"][-1]["body"] = "Human correction: the defect remains unresolved."
    base = "c" * 40
    result = post_review(
        client, 7, HEAD, base, BOT, feedback_body(base=base), lambda: None
    )
    assert result["status"] == "posted"
    assert len(state["reviews"]) == 3


@pytest.mark.parametrize(
    "outcome,findings",
    [("no_findings", ()), ("findings", (("defaults-dropped", "blocking"),))],
)
def test_inconclusive_review_can_be_completed_at_same_revision(
    endpoint, outcome, findings
):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(
        client, 7, HEAD, BASE, BOT, feedback_body(outcome="inconclusive"), lambda: None
    )
    result = post_review(
        client,
        7,
        HEAD,
        BASE,
        BOT,
        feedback_body(outcome=outcome, findings=findings),
        lambda: None,
    )
    assert result["status"] == "posted"
    assert len(state["reviews"]) == 2


def test_human_edit_to_hidden_finding_identity_invalidates_feedback():
    body = feedback_body(
        outcome="findings", findings=(("original-defect", "blocking"),)
    )
    assert read_feedback(body, 7, HEAD) is not None
    edited = body.replace("original-defect", "different-defect")
    assert read_feedback(edited, 7, HEAD) is None


@pytest.mark.parametrize(
    "value",
    [
        {"outcome": "findings"},
        {
            "outcome": "no_findings",
            "finding_ids": [{"id": "defect", "severity": "blocking"}],
        },
        {
            "outcome": "inconclusive",
            "finding_ids": [{"id": "defect", "severity": "blocking"}],
        },
        {
            "outcome": "findings",
            "finding_ids": [{"id": "defect", "severity": "blocking"}] * 2,
        },
    ],
)
def test_ambiguous_or_contradictory_outcomes_are_rejected(value):
    with pytest.raises(ValueError):
        ReviewFeedback.model_validate(value)


def test_legacy_plan_feedback_remains_explicitly_inconclusive():
    assert ReviewFeedback.model_validate({}).outcome == "inconclusive"
