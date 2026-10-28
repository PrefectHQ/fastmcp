import json
import threading
from copy import deepcopy
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from flows.agentic_review_app import ReviewApp, review_client
from flows.agentic_review_publication import before_delivery, post_review, review_body
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
            if self.path == "/app":
                self.app_auth()
                self.respond({"id": state["app_id"], "slug": "reviewer"})
            elif self.path.endswith("/installation"):
                self.app_auth()
                self.respond({"id": 10, "app_id": 42, "suspended_at": None})
            elif "/reviews?" in self.path:
                self.respond(state["reviews"])
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
            },
            "triage_review_run_id": "review-run",
        },
    )


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


def test_delivery_normalization_ignores_only_acknowledged_bot_reviews():
    original = {
        "prs": [{"number": 7, "updated_at": "before", "reviews": [], "timeline": []}]
    }
    current = deepcopy(original)
    current["prs"][0].update(
        updated_at="after",
        reviews=[{"id": 1, "user": {"login": BOT}}],
        timeline=[{"id": 1, "event": "reviewed", "actor": {"login": BOT}}],
    )
    receipts = [{"pr_number": 7, "review_id": 1}]
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
    current = deepcopy(original)
    current["prs"][0].update(
        updated_at="after",
        reviews=[{"id": 1, "user": {"login": BOT}}],
        timeline=[{"id": 1, "event": "reviewed", "actor": {"login": BOT}}],
    )
    number = original["issue"]["number"]
    result = {
        "bins": {
            "existing_review": [
                {
                    "issue_number": number,
                    "published_reviews": [
                        {"pr_number": 7, "review_id": 1, "reviewer_login": BOT}
                    ],
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
