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
        pds = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

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

    def post(event: str, payload: dict, secret="s3cret", delivery="d-1"):
        body = json.dumps(payload).encode()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/github",
            data=body,
            method="POST",
            headers={
                "X-GitHub-Event": event,
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": f"sha256={digest}",
            },
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
