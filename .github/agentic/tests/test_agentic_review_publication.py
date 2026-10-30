import hashlib
import json
import threading
from copy import deepcopy
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from flows.agentic_review_app import ReviewApp, review_client
from flows.agentic_review_publication import (
    before_delivery,
    format_review,
    post_review,
    review_body,
)
from flows.agentic_stack_publication import GitHub

HEAD = "a" * 40
BASE = "b" * 40
BOT = "reviewer[bot]"


@pytest.fixture
def endpoint():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    state = {
        "pr": {
            "number": 7,
            "head": {"sha": HEAD},
            "state": "open",
            "merged": False,
            "draft": False,
            "title": "Repair defaults",
            "body": "Fixes #1",
            "labels": [],
            "user": {"login": "reporter"},
        },
        "reviews": [],
        "comments": [],
        "review_comments": [],
        "posts": [],
        "revoked": False,
        "disconnect": False,
        "app_id": 42,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value, status=200):
            self.send_response(status)
            self.end_headers()
            if status != 204:
                self.wfile.write(json.dumps(value).encode())

        def app_auth(self):
            claims = jwt.decode(
                self.headers["Authorization"].removeprefix("Bearer "),
                private.public_key(),
                algorithms=["RS256"],
                issuer="42",
            )
            assert claims["exp"] - claims["iat"] == 360

        def do_GET(self):
            if callback := state.get("before_get"):
                callback(self.path)
            if self.path in state.get("routes", {}):
                self.respond(state["routes"][self.path])
            elif self.path == "/app":
                self.app_auth()
                self.respond({"id": state["app_id"], "slug": "reviewer"})
            elif self.path.endswith("/installation"):
                self.app_auth()
                self.respond({"id": 10, "app_id": 42, "suspended_at": None})
            elif "/reviews?" in self.path:
                self.respond(state["reviews"])
            elif "/issues/" in self.path and "/comments?" in self.path:
                self.respond(state["comments"])
            elif "/pulls/" in self.path and "/comments?" in self.path:
                self.respond(state["review_comments"])
            else:
                self.respond(state["pr"])

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posts"].append((self.path, body))
            if self.path == "/app/installations/10/access_tokens":
                self.app_auth()
                self.respond({"token": "test-installation"})
                return
            assert self.headers["Authorization"] == "Bearer test-installation"
            assert body["event"] == "COMMENT"
            review = {
                "id": len(state["reviews"]) + 1,
                "commit_id": body["commit_id"],
                "state": "COMMENTED",
                "body": body["body"],
                "user": {"login": BOT, "type": "Bot"},
                "html_url": "https://github.com/PrefectHQ/fastmcp/pull/7#review",
            }
            state["reviews"].append(review)
            if state["disconnect"]:
                state["disconnect"] = False
                self.close_connection = True
                return
            self.respond(review)

        def do_DELETE(self):
            assert self.path == "/installation/token"
            assert self.headers["Authorization"] == "Bearer test-installation"
            state["revoked"] = True
            self.respond(None, 204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}", pem
    server.shutdown()
    server.server_close()
    thread.join()


def body():
    return review_body(
        7,
        HEAD,
        BASE,
        {
            "issue_number": 1,
            "adversarial_review": {
                "decision": "review_existing",
                "reason": "Base fails; head passes the supported controls.",
                "findings": [],
                "next_action": "Run static checks at this head.",
                "public_reviews": [
                    {
                        "pr_number": 7,
                        "summary": "The supplied value now survives rendering.",
                        "requested_change": "",
                        "evidence": "Focused regression and controls pass.",
                    }
                ],
            },
            "triage_review_run_id": "review-run",
        },
    )


def test_review_keeps_only_public_feedback():
    rendered = body()
    visible, details = rendered.split("<details>", 1)
    assert "FastMCP triage reviewer bot" in visible
    assert "The supplied value now survives rendering." in visible
    assert "Focused regression and controls pass." in details
    assert "Base fails" not in rendered and "Run static checks" not in rendered
    assert "review-run" not in rendered
    assert rendered.count(HEAD) == rendered.count(BASE) == 1
    assert "<details open" not in rendered
    assert rendered.endswith("</details>")


def incident_result():
    return json.loads((Path(__file__).parent / "fixtures/review-5836.json").read_text())


def test_5836_review_contains_finding_without_internal_report():
    result = incident_result()
    rendered = review_body(5836, HEAD, BASE, result)
    assert len(rendered.split()) < 140
    assert "validation_alias" in rendered and "silently use defaults" in rendered
    assert "71 prompt-module tests pass" in rendered
    for private in [
        "Want:",
        "Commands actually executed",
        "Next action:",
        "uv run",
        "assignee",
        "triage review run",
        result["triage_review_run_id"],
    ]:
        assert private not in rendered
    assert rendered.count("required arguments error") == 1
    assert result["adversarial_review"]["reason"].startswith("Want:")


@pytest.mark.parametrize(
    "public",
    [None, [], [{"pr_number": 999}], [{"pr_number": 5836}, {"pr_number": 5836}]],
)
def test_publication_never_falls_back_to_private_report(public):
    result = incident_result()
    if public is None:
        result["adversarial_review"].pop("public_reviews")
    else:
        result["adversarial_review"]["public_reviews"] = public
    with pytest.raises(ValueError):
        review_body(5836, HEAD, BASE, result)


@pytest.mark.parametrize(
    "material",
    [
        "Want: internal",
        "uv run pytest tests",
        "Commands actually executed: fetch",
        "assignee-eligibility check",
        HEAD,
        "06ae39c2-6165-70d7-8000-b3e275d4f1da",
        "<details>internal</details>",
        "word " * 121,
    ],
)
def test_public_feedback_rejects_internal_material(material):
    result = incident_result()
    result["adversarial_review"]["public_reviews"][0]["summary"] = material
    with pytest.raises(ValueError):
        review_body(5836, HEAD, BASE, result)


def test_review_evidence_cannot_close_the_collapsed_section():
    evidence = "Evidence </details> escaped <SUMMARY>summary</SUMMARY>"
    rendered = format_review(7, HEAD, BASE, "Review recorded.", evidence)
    assert rendered.count("</details>") == 1
    assert "Evidence &lt;/details&gt; escaped &lt;SUMMARY&gt;" in rendered


def test_real_signed_app_auth_scopes_token_and_revokes_on_failure(endpoint):
    state, url, pem = endpoint
    app = ReviewApp(app_id=42, slug="reviewer", private_key_block="review-key")
    with pytest.raises(RuntimeError, match="worker failed"):
        with review_client(app, pem, url=url):
            raise RuntimeError("worker failed")
    assert state["revoked"]
    assert state["posts"][0] == (
        "/app/installations/10/access_tokens",
        {
            "repositories": ["fastmcp"],
            "permissions": {
                "pull_requests": "write",
                "contents": "read",
                "issues": "read",
                "checks": "read",
                "statuses": "read",
            },
        },
    )
    state["app_id"] = 99
    with pytest.raises(ValueError, match="configured identity"):
        with review_client(app, pem, url=url):
            pytest.fail("Wrong App admitted")
    assert len(state["posts"]) == 1


def test_lost_post_response_reconciles_one_revision_specific_review(endpoint):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    state["disconnect"] = True
    guards = []
    with pytest.raises(RemoteDisconnected):
        post_review(client, 7, HEAD, BASE, BOT, body(), lambda: guards.append(True))
    receipt = post_review(
        client, 7, HEAD, BASE, BOT, body(), lambda: pytest.fail("Already posted")
    )
    assert receipt["status"] == "already_posted"
    assert receipt["head_sha"] == HEAD
    assert len(state["reviews"]) == len(guards) == 1


@pytest.mark.parametrize(
    "change", ["head", "draft", "hold", "author", "closed", "freshness"]
)
def test_changes_and_holds_prevent_posts(endpoint, change):
    state, url, _ = endpoint

    def guard():
        if change == "freshness":
            raise ValueError("Fresh discussion changed")
        if change == "head":
            state["pr"]["head"]["sha"] = "c" * 40
        if change == "draft":
            state["pr"]["draft"] = True
        if change == "hold":
            state["pr"]["labels"] = [{"name": "do-not-merge"}]
        if change == "author":
            state["pr"]["user"]["login"] = BOT
        if change == "closed":
            state["pr"]["state"] = "closed"

    with pytest.raises(ValueError):
        post_review(GitHub("test-installation", url), 7, HEAD, BASE, BOT, body(), guard)
    assert not state["posts"]


def retained_delivery():
    review = {
        "id": 1,
        "state": "COMMENTED",
        "commit_id": HEAD,
        "body": "Retained review",
        "user": {"login": BOT, "type": "Bot"},
    }
    receipt = {
        "pr_number": 7,
        "review_id": 1,
        "reviewer_login": BOT,
        "head_sha": HEAD,
        "body_sha256": hashlib.sha256(review["body"].encode()).hexdigest(),
    }
    return review, receipt


def test_delivery_normalization_ignores_only_acknowledged_bot_reviews():
    original = {
        "prs": [{"number": 7, "updated_at": "before", "reviews": [], "timeline": []}]
    }
    review, receipt = retained_delivery()
    current = deepcopy(original)
    current["prs"][0].update(
        updated_at="after",
        reviews=[review],
        timeline=[
            {
                "id": 1,
                "event": "reviewed",
                "actor": {"id": None, "login": None, "type": None},
            }
        ],
    )
    receipts = [receipt]
    assert before_delivery(current, receipts, BOT) == before_delivery(
        original, receipts, BOT
    )
    current["prs"][0]["reviews"].append(
        {"id": 2, "user": {"login": "reporter"}, "body": "The contract changed"}
    )
    assert before_delivery(current, receipts, BOT) != before_delivery(
        original, receipts, BOT
    )
    assert len(current["prs"][0]["reviews"]) == 2


def test_posted_review_remains_current_but_new_human_evidence_invalidates(evidence):
    from flows.agentic_freshness import revalidate

    original = {
        **evidence,
        "stacks": [],
        "prs": [{"number": 7, "updated_at": "before", "reviews": [], "timeline": []}],
    }
    review, receipt = retained_delivery()
    current = deepcopy(original)
    current["prs"][0].update(
        updated_at="after",
        reviews=[review],
        timeline=[
            {
                "id": 1,
                "event": "reviewed",
                "actor": {"id": None, "login": None, "type": None},
            }
        ],
    )
    number = original["issue"]["number"]
    result = {
        "bins": {
            "existing_review": [
                {
                    "issue_number": number,
                    "published_reviews": [receipt],
                }
            ],
            "operational": [],
        }
    }
    assert (
        revalidate(result, {number: original}, {number: "main"}, lambda *_: current)
        == result
    )
    current["prs"][0]["reviews"].append(
        {"id": 2, "body": "New counterexample", "user": {"login": "reporter"}}
    )
    stale = revalidate(result, {number: original}, {number: "main"}, lambda *_: current)
    assert not stale["bins"]["existing_review"]
    assert stale["bins"]["operational"][0]["status"] == "stale"


def test_distinct_issue_workers_share_one_pr_publication_claim(endpoint):
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from prefect.testing.utilities import prefect_test_harness

    from flows.agentic_claims import claim_work

    state, url, _ = endpoint
    start = threading.Barrier(2)
    with prefect_test_harness():

        def publish():
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
                    BASE,
                    BOT,
                    body(),
                    lambda: None,
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, publish) for _ in range(2)
            ]
            receipts = [f.result(timeout=40) for f in futures]
    assert {r["status"] for r in receipts} == {"posted", "already_posted"}
    assert len(state["reviews"]) == 1


def test_history_only_review_retains_skip_without_loading_a_write_credential():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from prefect import flow
    from prefect.client.orchestration import get_client
    from prefect.testing.utilities import prefect_test_harness

    from flows.agentic_review_delivery import deliver_reviews
    from flows.agentic_stack_policy import Batch
    from flows.agentic_stack_runtime import Runtime, record

    @flow
    def controller():
        return None

    now = datetime.now(UTC)
    batch = Batch(
        id="history",
        started_at=now,
        expires_at=now + timedelta(minutes=5),
        groups=[
            {
                "name": "review",
                "rationale": "Retain prior contribution evidence",
                "base_sha": BASE,
                "branch": "main",
                "items": [{"issue_number": 1}],
            }
        ],
    )
    runtime = Runtime(
        plans={
            role: {"flow_id": uuid4(), "version_id": uuid4()}
            for role in ["assess", "reproduce", "prepare", "review", "triage_review"]
        },
        validator_id=uuid4(),
        source_sha=BASE,
        publish_reviews=True,
        reviewer_app={
            "app_id": 42,
            "slug": "reviewer",
            "private_key_block": "deliberately-not-provisioned",
        },
    )
    review = {
        "decision": "review_existing",
        "reason": "Historical gated contribution",
        "next_action": "Retain prior discussion",
        "findings": [],
    }
    outcome = {
        "issue_number": 1,
        "adversarial_review": review,
        "triage_review_run_id": "review-run",
        "verification": [],
    }
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(controller).id
        record.fn(
            owner,
            "history-issue-1-triage-review",
            {"run_id": "review-run", "result": review},
        )
        result = deliver_reviews.fn(
            owner, batch, batch.groups[0], runtime, outcome, {"prs": []}
        )
    assert result == {
        "posted": [],
        "skipped": [{"reason": "No verified contribution selected"}],
    }


@pytest.mark.parametrize("change", ["dismissal", "edit"])
def test_human_changes_to_posted_bot_review_are_not_hidden(change):
    review, receipt = retained_delivery()
    original = {
        "prs": [
            {
                "number": 7,
                "reviews": [review],
                "timeline": [{"id": 1, "event": "reviewed", "actor": None}],
            }
        ]
    }
    current = deepcopy(original)
    if change == "dismissal":
        current["prs"][0]["reviews"][0]["state"] = "DISMISSED"
    else:
        current["prs"][0]["reviews"][0]["body"] = "Edited after publication"
    assert before_delivery(original, [receipt], BOT) != before_delivery(
        current, [receipt], BOT
    )


def test_existing_review_with_different_feedback_is_not_adopted(endpoint):
    state, url, _ = endpoint
    client = GitHub("test-installation", url)
    post_review(client, 7, HEAD, BASE, BOT, body(), lambda: None)
    different = body().replace("now survives rendering", "is still dropped")
    with pytest.raises(ValueError, match="different review already exists"):
        post_review(client, 7, HEAD, BASE, BOT, different, lambda: None)
    assert len(state["reviews"]) == 1


def test_partial_delivery_failure_keeps_already_posted_receipts(monkeypatch):
    from contextlib import nullcontext
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from prefect import flow
    from prefect.client.orchestration import get_client
    from prefect.testing.utilities import prefect_test_harness

    from flows import agentic_review_delivery as delivery
    from flows.agentic_stack_policy import Batch
    from flows.agentic_stack_runtime import Runtime, record

    @flow
    def controller():
        return None

    posted = []

    def post(github, number, head, baseline, reviewer, body, guard):
        if posted:
            raise ValueError("Issue or contribution changed after review")
        posted.append({"pr_number": number, "head_sha": head, "status": "posted"})
        return posted[-1]

    monkeypatch.setattr(delivery, "review_client", lambda *_: nullcontext())
    monkeypatch.setattr(delivery, "secret_sync", lambda _: "key")
    monkeypatch.setattr(delivery, "claim_work", lambda *_: nullcontext())
    monkeypatch.setattr(delivery, "post_review", post)
    monkeypatch.setattr(delivery, "owner_alive", lambda *_: None)
    monkeypatch.setattr(delivery, "review_body", lambda *_: "review")
    now = datetime.now(UTC)
    batch = Batch(
        id="partial",
        started_at=now,
        expires_at=now + timedelta(minutes=5),
        groups=[
            {
                "name": "review",
                "rationale": "Deliver two verified contributions",
                "base_sha": BASE,
                "branch": "main",
                "items": [{"issue_number": 1}],
            }
        ],
    )
    runtime = Runtime(
        plans={
            role: {"flow_id": uuid4(), "version_id": uuid4()}
            for role in ["assess", "reproduce", "prepare", "review", "triage_review"]
        },
        validator_id=uuid4(),
        source_sha=BASE,
        publish_reviews=True,
        reviewer_app={"app_id": 42, "slug": "reviewer", "private_key_block": "k"},
    )
    review = {"decision": "review_existing", "reason": "r", "findings": []}
    prs = [
        {"number": n, "state": "open", "merged": False, "head_sha": HEAD}
        for n in (7, 8)
    ]
    outcome = {
        "issue_number": 1,
        "adversarial_review": review,
        "triage_review_run_id": "review-run",
        "verification": [
            {
                "pr_number": n,
                "status": "verification_recorded",
                "head_sha": HEAD,
                "baseline_sha": BASE,
            }
            for n in (7, 8)
        ],
    }
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(controller).id
        record.fn(
            owner,
            "partial-issue-1-triage-review",
            {"run_id": "review-run", "result": review},
        )
        with pytest.raises(delivery.PartialReviewDelivery) as failure:
            delivery.deliver_reviews.fn(
                owner,
                batch,
                batch.groups[0],
                runtime,
                outcome,
                {"prs": prs, "base_sha": BASE},
            )
    assert failure.value.posted == posted
    assert [r["pr_number"] for r in failure.value.posted] == [7]
