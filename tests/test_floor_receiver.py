import hashlib
import hmac
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "scripts" / "floor_receiver" / "main.py"


class FakePds:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []
        self.expire_once = False
        self.stored: dict | None = None
        pds = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self):
                if pds.stored is None:
                    status, out = 400, {"error": "RecordNotFound"}
                else:
                    status, out = 200, {"value": pds.stored}
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                auth = self.headers.get("Authorization", "")
                pds.calls.append((method, body, auth))
                status, out = 200, {"uri": "at://x", "cid": "c"}
                if method == "com.atproto.server.createSession":
                    out = {"did": "did:x", "accessJwt": "a1", "refreshJwt": "r1"}
                elif method == "com.atproto.server.refreshSession":
                    out = {"did": "did:x", "accessJwt": "a2", "refreshJwt": "r2"}
                elif pds.expire_once and auth == "Bearer a1":
                    pds.expire_once = False
                    status, out = 400, {"error": "ExpiredToken"}
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def puts(self) -> list[dict]:
        return [b for m, b, _ in self.calls if m.endswith("putRecord")]


@pytest.fixture
def receiver(monkeypatch):
    pds = FakePds()
    monkeypatch.delenv("PREFECT_TOKEN", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("ATPROTO_HANDLE", "prefect.io")
    monkeypatch.setenv("ATPROTO_PASSWORD", "pw")
    monkeypatch.setenv("ATPROTO_PDS", f"http://127.0.0.1:{pds.server.server_port}")
    spec = importlib.util.spec_from_file_location("floor_receiver", ROOT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(
        event: str,
        payload: dict,
        secret="s3cret",
        delivery="d-1",
        path="/github",
        bearer: str | None = None,
    ):
        body = json.dumps(payload).encode()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": f"sha256={digest}",
        }
        if bearer:
            headers = {"Authorization": f"Bearer {bearer}"}
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, ""

    yield post, pds
    server.shutdown()
    pds.server.shutdown()


REPO = {"full_name": "PrefectHQ/fastmcp"}
MERGED = {
    "action": "closed",
    "repository": REPO,
    "sender": {"login": "jane", "type": "User"},
    "pull_request": {"number": 7, "title": "fix", "merged": True, "html_url": "u"},
}


def test_signed_event_is_written_at_once(receiver):
    post, pds = receiver
    assert post("pull_request", MERGED, delivery="ab-12") == (202, "merged #7")
    (put,) = pds.puts()
    assert put["collection"] == "io.prefect.fastmcp.floor.event"
    assert put["rkey"] == "whab12"
    assert put["record"]["kind"] == "merged" and put["record"]["number"] == 7


def test_bad_signature_other_repo_and_unknown_events_write_nothing(receiver):
    post, pds = receiver
    assert post("pull_request", MERGED, secret="wrong")[0] == 401
    other = {**MERGED, "repository": {"full_name": "someone/else"}}
    assert post("pull_request", other) == (202, "other repository")
    assert post("push", {"repository": REPO}) == (202, "ignored")
    assert post("ping", {"zen": "hi"}) == (200, "pong")
    assert pds.puts() == []


def test_expired_token_refreshes_instead_of_logging_in(receiver):
    post, pds = receiver
    post("pull_request", MERGED, delivery="one")
    pds.expire_once = True
    assert post("pull_request", MERGED, delivery="two")[0] == 202
    methods = [m.rsplit(".", 1)[-1] for m, _, _ in pds.calls]
    assert methods.count("createSession") == 1
    assert "refreshSession" in methods
    assert [p["rkey"] for p in pds.puts()] == ["whone", "whtwo", "whtwo"]


def ci_run(status: str, updated: str, **run) -> dict:
    return {
        "action": status,
        "repository": REPO,
        "sender": {"login": "jane", "type": "User"},
        "workflow_run": {
            "id": 123,
            "name": "Tests",
            "head_branch": "main",
            "event": "push",
            "status": status,
            "conclusion": "failure" if status == "completed" else None,
            "head_sha": "abcdef1234",
            "display_title": "Fix the thing\n\nlong body",
            "html_url": "https://github.com/PrefectHQ/fastmcp/actions/runs/123",
            "run_started_at": "2026-11-23T07:00:00Z",
            "updated_at": updated,
            **run,
        },
    }


def test_ci_run_on_main_is_one_record_that_only_moves_forward(receiver):
    post, pds = receiver
    done = ci_run("completed", "2026-11-23T07:09:00Z")
    assert post("workflow_run", done, delivery="c1") == (202, "ci Tests completed")
    (put,) = pds.puts()
    assert put["collection"] == "io.prefect.fastmcp.floor.ci"
    assert put["rkey"] == "0000000000000123"
    assert put["record"]["conclusion"] == "failure"
    assert put["record"]["title"] == "Fix the thing"
    assert put["record"]["sha"] == "abcdef1"
    # a late "in_progress" delivery for the same run is older than what is stored
    pds.stored = put["record"]
    late = ci_run("in_progress", "2026-11-23T07:01:00Z")
    assert post("workflow_run", late, delivery="c2") == (
        202,
        "older than what is stored",
    )
    assert len(pds.puts()) == 1


def test_ci_off_main_or_from_prs_is_ignored(receiver):
    post, pds = receiver
    branch = ci_run("completed", "2026-11-23T07:09:00Z", head_branch="fix")
    pr = ci_run("completed", "2026-11-23T07:09:00Z", event="pull_request")
    assert post("workflow_run", branch)[1] == "nothing to show"
    assert post("workflow_run", pr)[1] == "nothing to show"
    assert pds.puts() == []


RUN = {
    "event": "prefect.flow-run.Running",
    "occurred": "2026-11-23T07:35:20.123456+00:00",
    "id": "0b6a8d2e-1111-4222-8333-444455556666",
    "flow": "fastmcp-triage-intake",
    "created": "2026-11-23T07:35:12.000001+00:00",
    "start": "2026-11-23T07:35:19+00:00",
    "params": {"config": {"issues": [6712, 6713], "sender": "jane"}, "token": "x"},
}


def test_prefect_run_needs_the_token_and_publishes_only_safe_fields(
    receiver, monkeypatch
):
    post, pds = receiver
    assert post_prefect(post, RUN, token=None)[0] == 401  # no token configured
    monkeypatch.setenv("PREFECT_TOKEN", "tok")
    assert post_prefect(post, RUN, token="nope")[0] == 401
    assert post_prefect(post, RUN, token="tok") == (
        202,
        "run fastmcp-triage-intake running",
    )
    (put,) = pds.puts()
    assert put["collection"] == "io.prefect.fastmcp.floor.run"
    assert put["rkey"] == "20261123T073512-0b6a8d2e"
    record = put["record"]
    assert record["numbers"] == [6712, 6713]
    assert set(record) == {
        "$type",
        "flow",
        "state",
        "numbers",
        "createdAt",
        "startedAt",
        "at",
    }
    other = {**RUN, "flow": "someone-elses-flow"}
    assert post_prefect(post, other, token="tok")[1] == "nothing to show"


def post_prefect(post, payload: dict, token: str | None):
    return post("prefect", payload, path="/prefect", bearer=token)
