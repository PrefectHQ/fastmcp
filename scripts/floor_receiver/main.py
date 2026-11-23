"""GitHub webhooks -> triage floor event records on prefect.io, as they happen.

GitHub posts each issue, PR, comment, review, release and workflow run event
here, and a Prefect Cloud automation posts each triage flow run's state
changes to /prefect. The
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
    PREFECT_TOKEN     bearer token the Prefect Cloud automation sends to /prefect
    PORT              set by the host

On App Engine the two secrets come from Secret Manager instead (see app.yaml):
set SECRETS_PROJECT and the instance's service account reads
floor-receiver-webhook-secret, floor-receiver-atproto-password and (if it
exists) floor-receiver-prefect-token at startup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# In the image the feed script sits next to this file; in the repo, one level up.
sys.path.append(str(Path(__file__).resolve().parent.parent))
from triage_floor_feed import NSID, REPO, to_ci, to_event, to_run  # noqa: E402

MAX_BODY = 5 * 1024 * 1024  # GitHub caps webhook payloads at 25MB; ours are small
# GitHub event names the feed script knows how to turn into records
EVENTS = {
    "issues",
    "pull_request",
    "issue_comment",
    "pull_request_review",
    "release",
    "workflow_run",
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
        with urllib.request.urlopen(request, timeout=4) as response:
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


def put(body: dict, token: str) -> None:
    # one quick retry for a PDS blip; both tries fit inside GitHub's 10s timeout
    try:
        xrpc("com.atproto.repo.putRecord", body, token)
    except (PdsError, OSError) as error:
        if isinstance(error, PdsError) and error.status < 500:
            raise
        time.sleep(0.5)
        xrpc("com.atproto.repo.putRecord", body, token)


def expired(error: PdsError) -> bool:
    return error.error in {"ExpiredToken", "InvalidToken"} or error.status == 401


def get_record(collection: str, rkey: str, session: dict) -> dict | None:
    request = urllib.request.Request(
        f"{os.environ['ATPROTO_PDS'].rstrip('/')}/xrpc/com.atproto.repo.getRecord?"
        + urllib.parse.urlencode(
            {"repo": session["did"], "collection": collection, "rkey": rkey}
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.loads(response.read()).get("value")
    except urllib.error.HTTPError as exc:
        if exc.code == 400:  # RecordNotFound
            return None
        raise PdsError("com.atproto.repo.getRecord", exc.code, None) from None


NEWER_ONLY = threading.Lock()


def write(collection: str, rkey: str, record: dict, newer_only: bool = False) -> bool:
    """Put one record, logging in again once if the session went stale.

    With newer_only, a record whose `at` is older than the stored one is
    dropped: webhooks can arrive out of order, and a late "in progress" must
    not overwrite "completed". Returns whether it was written.
    """
    if newer_only:
        # one read-check-write at a time, so two overlapping deliveries for the
        # same run can't both pass the check (the app runs one instance)
        with NEWER_ONLY:
            stored = get_record(collection, rkey, SESSION.get())
            if stored and str(stored.get("at", "")) > str(record.get("at", "")):
                return False
            return write(collection, rkey, record)
    session = SESSION.get()
    body = {
        "repo": session["did"],
        "collection": collection,
        "rkey": rkey,
        "record": record,
        "validate": False,
    }
    try:
        put(body, session["accessJwt"])
    except PdsError as error:
        if not expired(error):
            raise
        session = SESSION.get(stale=session["accessJwt"])
        put(body, session["accessJwt"])
    return True


def publish(name: str, delivery: str, payload: dict) -> str:
    """Write the record for one event. Returns what happened, for the log."""
    if (payload.get("repository") or {}).get("full_name") != REPO:
        return "other repository"
    if name == "workflow_run":
        ci = to_ci(payload)
        if ci is None:
            return "nothing to show"
        rkey, record = ci
        if not write(f"{NSID}.ci", rkey, record, newer_only=True):
            return "older than what is stored"
        return f"ci {record['workflow']} {record['status']}"
    record = to_event(name, payload, datetime.now(UTC))
    if record is None:
        return "nothing to show"
    write(f"{NSID}.event", rkey_for(delivery), record)
    return f"{record['kind']} #{record.get('number', '')}"


def publish_run(body: dict) -> str:
    """Write the floor.run record for one Prefect flow-run state change."""
    run = to_run(body)
    if run is None:
        return "nothing to show"
    rkey, record = run
    if not write(f"{NSID}.run", rkey, record, newer_only=True):
        return "older than what is stored"
    return f"run {record['flow']} {record['state']}"


def prefect_token_ok(header: str | None) -> bool:
    token = os.environ.get("PREFECT_TOKEN", "")
    return bool(token) and hmac.compare_digest(f"Bearer {token}", header or "")


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
        # not /healthz: App Engine reserves paths ending in "z"
        self._reply(200 if self.path == "/health" else 404, "ok")

    def do_POST(self) -> None:
        if self.path not in {"/github", "/prefect"}:
            return self._reply(404)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(413)
        body = self.rfile.read(length)
        if self.path == "/prefect":
            return self._prefect(body)
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
            # 502 marks it failed in the hook's delivery log, where it can be
            # redelivered; the Action's snapshot still corrects the floor's state
            return self._reply(502)
        log.info("delivery %s %s: %s", delivery, name, outcome)
        self._reply(202, outcome)

    def _prefect(self, body: bytes) -> None:
        # a Prefect Cloud automation posts each triage flow run's state changes here
        if not prefect_token_ok(self.headers.get("Authorization")):
            log.warning("rejected a Prefect call with a bad token")
            return self._reply(401)
        try:
            payload = json.loads(body)
            outcome = publish_run(payload if isinstance(payload, dict) else {})
        except (PdsError, OSError, ValueError, KeyError) as error:
            log.error("prefect call failed: %s", error)
            return self._reply(502)
        log.info("prefect: %s", outcome)
        self._reply(202, outcome)


SECRETS = {
    "WEBHOOK_SECRET": "floor-receiver-webhook-secret",
    "ATPROTO_PASSWORD": "floor-receiver-atproto-password",
    "PREFECT_TOKEN": "floor-receiver-prefect-token",
}
# without a Prefect token the /prefect endpoint refuses everything; GitHub still works
OPTIONAL = {"PREFECT_TOKEN"}
METADATA = "http://metadata.google.internal/computeMetadata/v1"


def load_secrets() -> None:
    """Fill missing secret env vars from Secret Manager, as the instance's account."""
    project = os.environ.get("SECRETS_PROJECT")
    missing = [name for name in SECRETS if not os.environ.get(name)]
    if not project or not missing:
        return
    token_request = urllib.request.Request(
        f"{METADATA}/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(token_request, timeout=5) as response:
        token = json.loads(response.read())["access_token"]
    for name in missing:
        request = urllib.request.Request(
            "https://secretmanager.googleapis.com/v1/projects/"
            f"{project}/secrets/{SECRETS[name]}/versions/latest:access",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read())["payload"]["data"]
        except urllib.error.HTTPError:
            if name in OPTIONAL:
                log.warning("no Prefect token configured; /prefect stays off")
                continue
            raise
        os.environ[name] = base64.b64decode(data).decode().strip()


def main() -> None:
    load_secrets()
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
