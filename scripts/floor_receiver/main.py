"""GitHub webhooks -> triage floor event records on prefect.io, as they happen.

GitHub posts each issue, PR, comment, review and release event here. The
receiver checks GitHub's signature, turns the event into the same
`io.prefect.fastmcp.floor.event` record the triage-floor-feed Action writes
(using that script's own `to_event`), and writes it to the PDS straight away.
Viewers get it over Jetstream about a second after the event, instead of after
an Actions runner boots.

Stdlib only, so the image stays small and cold starts stay fast on Cloud Run.

Environment:
    WEBHOOK_SECRET    the secret configured on the GitHub webhook
    ATPROTO_HANDLE    prefect.io
    ATPROTO_PDS       https://witchesbutter.us-west.host.bsky.network
    ATPROTO_PASSWORD  an app password for that account
    PORT              set by Cloud Run
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# In the image the feed script sits next to this file; in the repo, one level up.
sys.path.append(str(Path(__file__).resolve().parent.parent))
from triage_floor_feed import NSID, REPO, to_event  # noqa: E402

MAX_BODY = 5 * 1024 * 1024  # GitHub caps webhook payloads at 25MB; ours are small
# GitHub event names the feed script knows how to turn into records
EVENTS = {
    "issues",
    "pull_request",
    "issue_comment",
    "pull_request_review",
    "release",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("floor-receiver")


class PdsError(RuntimeError):
    def __init__(self, method: str, status: int, error: str | None) -> None:
        super().__init__(f"{method} failed with HTTP {status} ({error})")
        self.status = status
        self.error = error


def xrpc(method: str, body: dict, token: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{os.environ['ATPROTO_PDS'].rstrip('/')}/xrpc/{method}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # never log the body: it can carry tokens; the error name is safe
        try:
            error = json.loads(exc.read() or b"{}").get("error")
        except ValueError:
            error = None
        raise PdsError(method, exc.code, error) from None


class Session:
    """One PDS session per instance, refreshed rather than re-created.

    bsky-hosted PDSes allow 300 logins a day per account. An instance logs in
    once when it starts and refreshes after that.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: dict | None = None

    def get(self, stale: str | None = None) -> dict:
        with self._lock:
            if self._session and self._session["accessJwt"] != stale:
                return self._session
            fresh = None
            if self._session:
                try:
                    fresh = xrpc(
                        "com.atproto.server.refreshSession",
                        {},
                        self._session["refreshJwt"],
                    )
                except PdsError:
                    fresh = None
            if fresh is None:
                fresh = xrpc(
                    "com.atproto.server.createSession",
                    {
                        "identifier": os.environ["ATPROTO_HANDLE"],
                        "password": os.environ["ATPROTO_PASSWORD"],
                    },
                )
                log.info("logged in to the PDS")
            self._session = {
                "did": fresh["did"],
                "accessJwt": fresh["accessJwt"],
                "refreshJwt": fresh["refreshJwt"],
            }
            return self._session


SESSION = Session()


def signed(body: bytes, signature: str | None) -> bool:
    secret = os.environ["WEBHOOK_SECRET"].encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def rkey_for(delivery: str) -> str:
    # one record per GitHub delivery: a redelivery overwrites, never duplicates
    return "wh" + re.sub(r"[^A-Za-z0-9]", "", delivery)[:64]


def publish(name: str, delivery: str, payload: dict) -> str:
    """Write the record for one event. Returns what happened, for the log."""
    if (payload.get("repository") or {}).get("full_name") != REPO:
        return "other repository"
    record = to_event(name, payload, datetime.now(UTC))
    if record is None:
        return "nothing to show"
    session = SESSION.get()
    body = {
        "repo": session["did"],
        "collection": f"{NSID}.event",
        "rkey": rkey_for(delivery),
        "record": record,
        "validate": False,
    }
    try:
        xrpc("com.atproto.repo.putRecord", body, session["accessJwt"])
    except PdsError as error:
        if error.error not in {"ExpiredToken", "InvalidToken"} and error.status != 401:
            raise
        session = SESSION.get(stale=session["accessJwt"])
        xrpc("com.atproto.repo.putRecord", body, session["accessJwt"])
    return f"{record['kind']} #{record.get('number', '')}"


class Handler(BaseHTTPRequestHandler):
    server_version = "floor-receiver"

    def log_message(self, format: str, *args: object) -> None:  # quiet access log
        pass

    def _reply(self, status: int, text: str = "") -> None:
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._reply(200 if self.path == "/healthz" else 404, "ok")

    def do_POST(self) -> None:
        if self.path != "/github":
            return self._reply(404)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(413)
        body = self.rfile.read(length)
        if not signed(body, self.headers.get("X-Hub-Signature-256")):
            log.warning("rejected a delivery with a bad signature")
            return self._reply(401)
        name = self.headers.get("X-GitHub-Event", "")
        delivery = self.headers.get("X-GitHub-Delivery", "")
        if name == "ping":
            return self._reply(200, "pong")
        if name not in EVENTS or not delivery:
            return self._reply(202, "ignored")
        try:
            outcome = publish(name, delivery, json.loads(body))
        except (PdsError, OSError, ValueError, KeyError) as error:
            log.error("delivery %s failed: %s", delivery, error)
            # 502 lets GitHub's delivery log show it; the Action is the backstop
            return self._reply(502)
        log.info("delivery %s %s: %s", delivery, name, outcome)
        self._reply(202, outcome)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
