"""Who is working on what: claims on fastmcp work, as public atproto records.

Every agent that works on fastmcp (an interactive Claude or Codex session, a
triage workflow run, anything else) claims its target before it starts, reports
its phase while it works, and releases the claim with an outcome when it stops.
They do that through the triage MCP server's `claim_work`, `report_progress` and
`release_work` tools; this module is what those tools run, and it holds no
credential of its own.

Two collections on the prefect.io account carry the state:

- `io.prefect.fastmcp.work.claim`, one record per target (rkey `issue-6627`,
  `pr-6680`, `deploy-triage-stack`). It is the lock. A new claim is a
  `putRecord` with `swapRecord: null`, which the PDS refuses if the record
  exists; every later change swaps on the CID it read. Each new holder bumps
  `fence`, and progress or release with an old fence is refused, so a holder
  whose lease ran out can't write over the agent that took the work after it.
- `io.prefect.fastmcp.work.activity`, an append-only log with TID rkeys:
  claimed, progress, released and expired. The triage floor draws it, the
  `work.activity` MCP event serves it, and triage memory can index it.

A lease lasts 30 minutes and every progress report renews it. Nothing runs a
timer: whoever next reads a lapsed claim marks it expired (with the same
compare-and-swap) and logs it, and an expiry counts as a failed attempt.

Only public facts go out: the target, a phase and an outcome from fixed lists,
which agent kind, a hash of the worker id, and PR or issue numbers. No free
text, so there's nowhere for evidence, model output or private detail to leak.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

CLAIM = "io.prefect.fastmcp.work.claim"
ACTIVITY = "io.prefect.fastmcp.work.activity"
LEASE = timedelta(minutes=30)
ACCESS_TTL = timedelta(minutes=90)  # bsky access tokens live about two hours

TARGET_PATTERN = r"^(issue|pr):[1-9][0-9]{0,6}$|^deploy:(triage-stack|memory-server)$"
AGENTS = ("triage", "claude", "codex", "agent")
PHASES = (
    "reading",
    "assessing",
    "reproducing",
    "reviewing",
    "editing",
    "testing",
    "pushing",
    "posting",
    "waiting",
)
# What happened to the work. Every claim ends in exactly one of these.
OUTCOMES = ("pr", "handed_back", "abandoned")
# Why work was handed back, named after the triage outcomes in
# cloud-consolidation/outcome-labels.md so the two read the same.
REASONS = (
    "review_linked_pr",
    "get_more_evidence",
    "decide_direction",
    "propose_closure",
    "possible_duplicate",
    "nothing_to_do",
    "blocked",
)

Agent = Literal["triage", "claude", "codex", "agent"]
Phase = Literal[
    "reading",
    "assessing",
    "reproducing",
    "reviewing",
    "editing",
    "testing",
    "pushing",
    "posting",
    "waiting",
]
Outcome = Literal["pr", "handed_back", "abandoned"]
Reason = Literal[
    "review_linked_pr",
    "get_more_evidence",
    "decide_direction",
    "propose_closure",
    "possible_duplicate",
    "nothing_to_do",
    "blocked",
]

logger = logging.getLogger(__name__)


class Conflict(Exception):
    """Someone else changed the record between our read and our write."""


class PdsError(RuntimeError):
    pass


def stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def holder_of(worker: str) -> str:
    """The public stand-in for a worker id: stable, short, not reversible."""
    return hashlib.sha256(f"fastmcp-work:{worker}".encode()).hexdigest()[:16]


def rkey_of(target: str) -> str:
    if not re.match(TARGET_PATTERN, target):
        raise ValueError(f"Not a work target: {target!r}")
    return target.replace(":", "-")


def target_of(rkey: str) -> str:
    kind, _, rest = rkey.partition("-")
    return f"{kind}:{rest}"


_TID_CHARS = "234567abcdefghijklmnopqrstuvwxyz"
_last_tid = 0
_clock_id = random.randrange(1024)


def tid() -> str:
    """A timestamp id, the rkey scheme atproto logs use: sorts by time."""
    global _last_tid
    micros = max(time.time_ns() // 1000, _last_tid + 1)
    _last_tid = micros
    value = (micros << 10) | _clock_id
    out = ""
    for _ in range(13):
        out = _TID_CHARS[value & 31] + out
        value >>= 5
    return out


class Repo:
    """The prefect.io repo on its PDS: reads are public, writes log in.

    The session is cached through `read_session`/`write_session` (a Secret
    block for flows, memory for the MCP server) because bsky-hosted PDSes allow
    300 logins a day per account, shared with the GitHub feed workflow.
    """

    def __init__(
        self,
        *,
        password: Callable[[], str | None],
        read_session: Callable[[], str | None],
        write_session: Callable[[str], None],
        handle: str = "prefect.io",
        pds: str = "https://witchesbutter.us-west.host.bsky.network",
        did: str = "did:plc:lw3xovv4fu42jcxe4vt7glsg",
        client: httpx.Client | None = None,
    ) -> None:
        self.handle = handle
        self.base = pds.rstrip("/") + "/xrpc/"
        self.did = did
        self._password = password
        self._read_session = read_session
        self._write_session = write_session
        self._client = client or httpx.Client(timeout=10)
        self._session: dict | None = None

    # ---- reads (public, no session) ----

    def get(self, collection: str, rkey: str) -> tuple[dict, str] | None:
        response = self._client.get(
            self.base + "com.atproto.repo.getRecord",
            params={"repo": self.did, "collection": collection, "rkey": rkey},
        )
        if response.status_code == 400 and _error(response) == "RecordNotFound":
            return None
        _check(response, "getRecord")
        body = response.json()
        return body["value"], body["cid"]

    def records(self, collection: str, limit: int = 100) -> list[tuple[str, dict, str]]:
        """The newest `limit` records, as (rkey, value, cid), newest first."""
        response = self._client.get(
            self.base + "com.atproto.repo.listRecords",
            params={"repo": self.did, "collection": collection, "limit": limit},
        )
        _check(response, "listRecords")
        rows = [
            (row["uri"].rsplit("/", 1)[-1], row["value"], row["cid"])
            for row in response.json().get("records", [])
        ]
        return sorted(rows, key=lambda row: row[0], reverse=True)

    # ---- writes ----

    def put(self, collection: str, rkey: str, record: dict, swap: str | None) -> str:
        """Write `record` only if the current CID is `swap` (None: no record yet)."""
        body = {
            "repo": self.did,
            "collection": collection,
            "rkey": rkey,
            "record": record,
            "validate": False,
            "swapRecord": swap,
        }
        return self._write("com.atproto.repo.putRecord", body)["cid"]

    def append(self, collection: str, record: dict) -> str:
        rkey = tid()
        body = {
            "repo": self.did,
            "collection": collection,
            "rkey": rkey,
            "record": record,
            "validate": False,
        }
        self._write("com.atproto.repo.putRecord", body)
        return rkey

    def _write(self, method: str, body: dict) -> dict:
        for attempt in range(2):
            session = self._login(fresh=attempt > 0)
            response = self._client.post(
                self.base + method,
                json=body,
                headers={"Authorization": f"Bearer {session['accessJwt']}"},
            )
            error = _error(response)
            if error == "InvalidSwap":
                raise Conflict(method)
            if error in {"ExpiredToken", "InvalidToken"} or response.status_code == 401:
                continue
            _check(response, method)
            return response.json()
        raise PdsError(f"{method} refused a fresh session")

    def _login(self, fresh: bool = False) -> dict:
        now = datetime.now(UTC)
        if self._session and not fresh:
            return self._session
        cached: dict = {}
        if not fresh and (raw := self._read_session()):
            try:
                cached = json.loads(raw)
            except ValueError:
                cached = {}
        if (
            cached.get("accessJwt")
            and cached.get("at")
            and now - datetime.fromisoformat(cached["at"]) < ACCESS_TTL
        ):
            self._session = cached
            return cached
        password = self._password()
        if not password:
            raise PdsError("No password for the work records account")
        response = self._client.post(
            self.base + "com.atproto.server.createSession",
            json={"identifier": self.handle, "password": password},
        )
        _check(response, "createSession")
        body = response.json()
        self._session = {
            "accessJwt": body["accessJwt"],
            "refreshJwt": body["refreshJwt"],
            "at": now.isoformat(),
        }
        try:
            self._write_session(json.dumps(self._session))
        except (ValueError, RuntimeError, OSError, httpx.HTTPError):
            logger.warning("Could not cache the work records session")
        return self._session


def _error(response: httpx.Response) -> str | None:
    if response.status_code < 400:
        return None
    try:
        return response.json().get("error")
    except ValueError:
        return None


def _check(response: httpx.Response, method: str) -> None:
    if response.status_code >= 400:
        # never put the body in the message: it can carry tokens
        raise PdsError(f"{method} failed with HTTP {response.status_code}")


@dataclass(frozen=True)
class Claim:
    target: str
    holder: str
    agent: str
    phase: str
    fence: int
    state: str  # held, released, expired
    claimed_at: datetime
    expires_at: datetime
    cid: str
    outcome: str | None = None
    pr: int | None = None

    @classmethod
    def of(cls, value: dict, cid: str) -> Claim:
        return cls(
            target=value["target"],
            holder=value["holder"],
            agent=value.get("agent", "agent"),
            phase=value.get("phase", "reading"),
            fence=int(value.get("fence", 0)),
            state=value.get("state", "held"),
            claimed_at=parse(value["claimedAt"]),
            expires_at=parse(value["expiresAt"]),
            cid=cid,
            outcome=value.get("outcome"),
            pr=value.get("pr"),
        )

    def live(self, now: datetime) -> bool:
        return self.state == "held" and self.expires_at > now

    def public(self) -> dict:
        return {
            "target": self.target,
            "agent": self.agent,
            "phase": self.phase,
            "since": stamp(self.claimed_at),
            "expires_at": stamp(self.expires_at),
            **({"pr": self.pr} if self.pr else {}),
        }


class Board:
    """Claims and the activity log over a `Repo`."""

    def __init__(
        self, repo: Repo, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.repo = repo
        self.clock = clock

    def current(self, target: str) -> Claim | None:
        found = self.repo.get(CLAIM, rkey_of(target))
        return Claim.of(*found) if found else None

    def claims(self) -> list[Claim]:
        """Live claims, after marking any lapsed ones expired."""
        now = self.clock()
        live = []
        for _, value, cid in self.repo.records(CLAIM):
            claim = Claim.of(value, cid)
            if claim.live(now):
                live.append(claim)
            elif claim.state == "held":
                self._expire(claim)
        return live

    def claim(self, target: str, worker: str, agent: str) -> dict:
        holder = holder_of(worker)
        agent = agent if agent in AGENTS else "agent"
        for _ in range(3):
            now = self.clock()
            current = self.current(target)
            if current and current.state == "held" and not current.live(now):
                self._expire(current)
                continue
            if current and current.live(now):
                if current.holder != holder:
                    return {"claimed": False, "held_by": current.public()}
                # claiming again what you already hold renews it
                try:
                    return self._granted(self._update(current, phase=current.phase))
                except Conflict:
                    continue
            fence = current.fence + 1 if current else 1
            record = self._claim_record(
                target, holder, agent, "reading", fence, "held", now, now + LEASE
            )
            try:
                cid = self.repo.put(
                    CLAIM, rkey_of(target), record, current.cid if current else None
                )
            except Conflict:
                continue
            self._log("claimed", record)
            return self._granted(Claim.of(record, cid))
        return {"claimed": False, "reason": "contended; try again"}

    def progress(
        self,
        target: str,
        worker: str,
        fence: int,
        phase: str,
        pr: int | None = None,
    ) -> dict:
        if phase not in PHASES:
            raise ValueError(f"Unknown phase {phase!r}")
        for _ in range(3):
            current = self._held(target, worker, fence)
            if isinstance(current, dict):
                return current
            changed = phase != current.phase or (pr and pr != current.pr)
            try:
                renewed = self._update(current, phase=phase, pr=pr or current.pr)
            except Conflict:
                continue
            if changed:
                self._log("progress", self._record_of(renewed))
            return {"ok": True, "expires_at": stamp(renewed.expires_at)}
        return {"ok": False, "reason": "contended; try again"}

    def release(
        self,
        target: str,
        worker: str,
        fence: int,
        outcome: str,
        reason: str | None = None,
        pr: int | None = None,
        used_related: Iterable[int] = (),
    ) -> dict:
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {outcome!r}")
        if outcome == "pr" and not pr:
            raise ValueError("outcome 'pr' needs the PR's number")
        if outcome == "handed_back" and reason not in REASONS:
            raise ValueError(f"outcome 'handed_back' needs a reason: {REASONS}")
        for _ in range(3):
            current = self._held(target, worker, fence)
            if isinstance(current, dict):
                return current
            record = {
                **self._record_of(current),
                "state": "released",
                "outcome": outcome,
                "updatedAt": stamp(self.clock()),
                **({"pr": int(pr)} if pr else {}),
                **({"reason": reason} if outcome == "handed_back" else {}),
            }
            try:
                self.repo.put(CLAIM, rkey_of(target), record, current.cid)
            except Conflict:
                continue
            used = sorted({int(n) for n in used_related})[:20]
            self._log("released", {**record, **({"usedRelated": used} if used else {})})
            return {"ok": True, "outcome": outcome}
        return {"ok": False, "reason": "contended; try again"}

    def activity(self, after: str | None, limit: int = 100) -> tuple[list[dict], str]:
        """Log entries after the cursor `after`, oldest first, and the new cursor.

        With no cursor, returns nothing and the current end of the log, so a new
        subscriber starts from now.
        """
        self.claims()  # mark lapsed claims expired, so expiry shows up here
        rows = self.repo.records(ACTIVITY, limit)
        if after is None:
            return [], rows[0][0] if rows else ""
        newer = [(rkey, value) for rkey, value, _ in rows if rkey > after]
        newer.reverse()
        entries = [{"id": rkey, **_public_entry(value)} for rkey, value in newer]
        return entries, newer[-1][0] if newer else after

    def history(self, target: str, limit: int = 10) -> list[dict]:
        rows = self.repo.records(ACTIVITY, 100)
        mine = [_public_entry(v) for _, v, _ in rows if v.get("target") == target]
        return mine[:limit]

    # ---- internals ----

    def _held(self, target: str, worker: str, fence: int) -> Claim | dict:
        current = self.current(target)
        now = self.clock()
        if (
            current is None
            or current.holder != holder_of(worker)
            or current.fence != fence
        ):
            return {"ok": False, "reason": "you don't hold this claim; claim it again"}
        if not current.live(now):
            if current.state == "held":
                self._expire(current)
            return {"ok": False, "reason": "your claim lapsed; claim it again"}
        return current

    def _update(self, claim: Claim, *, phase: str, pr: int | None = None) -> Claim:
        now = self.clock()
        record = {
            **self._record_of(claim),
            "phase": phase,
            "expiresAt": stamp(now + LEASE),
            "updatedAt": stamp(now),
            **({"pr": int(pr)} if pr else {}),
        }
        cid = self.repo.put(CLAIM, rkey_of(claim.target), record, claim.cid)
        return Claim.of(record, cid)

    def _expire(self, claim: Claim) -> None:
        record = {
            **self._record_of(claim),
            "state": "expired",
            "updatedAt": stamp(self.clock()),
        }
        try:
            self.repo.put(CLAIM, rkey_of(claim.target), record, claim.cid)
        except Conflict:
            return  # someone else expired or took it first
        self._log("expired", record)

    def _granted(self, claim: Claim) -> dict:
        return {
            "claimed": True,
            "fence": claim.fence,
            "expires_at": stamp(claim.expires_at),
        }

    def _claim_record(
        self,
        target: str,
        holder: str,
        agent: str,
        phase: str,
        fence: int,
        state: str,
        claimed: datetime,
        expires: datetime,
    ) -> dict:
        return {
            "$type": CLAIM,
            "target": target,
            "holder": holder,
            "agent": agent,
            "phase": phase,
            "fence": fence,
            "state": state,
            "claimedAt": stamp(claimed),
            "expiresAt": stamp(expires),
            "updatedAt": stamp(claimed),
        }

    def _record_of(self, claim: Claim) -> dict:
        record = self._claim_record(
            claim.target,
            claim.holder,
            claim.agent,
            claim.phase,
            claim.fence,
            claim.state,
            claim.claimed_at,
            claim.expires_at,
        )
        if claim.pr:
            record["pr"] = claim.pr
        return record

    def _log(self, kind: str, claim_record: dict) -> None:
        entry = {
            "$type": ACTIVITY,
            "kind": kind,
            "createdAt": stamp(self.clock()),
            **{
                key: claim_record[key]
                for key in (
                    "target",
                    "holder",
                    "agent",
                    "phase",
                    "fence",
                    "pr",
                    "outcome",
                    "reason",
                    "usedRelated",
                )
                if key in claim_record
            },
        }
        try:
            self.repo.append(ACTIVITY, entry)
        except (PdsError, httpx.HTTPError) as error:
            # the claim record is the truth; the log is for watchers
            logger.warning("Work activity log write failed: %s", type(error).__name__)


def _public_entry(value: dict) -> dict:
    keys = (
        "kind",
        "target",
        "holder",
        "agent",
        "phase",
        "fence",
        "pr",
        "outcome",
        "reason",
        "usedRelated",
        "createdAt",
    )
    return {key: value[key] for key in keys if key in value}


def needs_retriage(entries: Iterable[dict]) -> list[int]:
    """Issues whose latest attempt failed or came back from someone other than
    triage: expired, abandoned, or handed back by an interactive agent."""
    latest: dict[str, dict] = {}
    for entry in entries:
        if entry.get("kind") in {"released", "expired", "claimed"}:
            latest[entry["target"]] = entry
    found = []
    for target, entry in latest.items():
        if not target.startswith("issue:"):
            continue
        failed = entry["kind"] == "expired" or entry.get("outcome") == "abandoned"
        handed = (
            entry.get("outcome") == "handed_back" and entry.get("agent") != "triage"
        )
        if failed or handed:
            found.append(int(target.split(":", 1)[1]))
    return sorted(found)


# ---------- the triage workflow's side: the same tools, over MCP ----------

# triage bins -> how a triage pass releases its claim
BIN_RELEASE: dict[str, tuple[str, str | None]] = {
    "existing_review": ("handed_back", "review_linked_pr"),
    "evidence": ("handed_back", "get_more_evidence"),
    "decisions": ("handed_back", "decide_direction"),
    "pruning": ("handed_back", "propose_closure"),
    "unchanged": ("handed_back", "nothing_to_do"),
    "operational": ("abandoned", None),
}


class WorkClaims:
    """A triage run claiming, reporting and releasing items through the triage
    MCP server, exactly as an interactive agent does.

    `call(tool, arguments) -> dict` is the MCP call; None turns claims off. If
    the server can't be reached the run carries on without claims (Prefect's
    issue leases still keep triage runs apart) and stops trying.
    """

    def __init__(self, call: Callable[[str, dict], dict] | None, worker: str) -> None:
        self._call = call
        self.worker = worker
        self.fences: dict[int, int] = {}

    def _try(self, tool: str, arguments: dict) -> dict | None:
        if self._call is None:
            return None
        try:
            return self._call(tool, arguments)
        except Exception as error:  # noqa: BLE001 - any client failure means off
            logger.warning("Work claims are off for this run: %s", type(error).__name__)
            self._call = None
            return None

    def claim(self, number: int) -> dict | None:
        """None when we hold the issue (or claims are off), else who holds it."""
        result = self._try(
            "claim_work",
            {"target": f"issue:{number}", "worker": self.worker, "agent": "triage"},
        )
        if result is None:
            return None
        if result.get("claimed"):
            self.fences[number] = result["fence"]
            return None
        return result.get("held_by") or {"reason": result.get("reason", "")}

    def progress(self, number: int, phase: str) -> None:
        if number in self.fences:
            self._try(
                "report_progress",
                {
                    "target": f"issue:{number}",
                    "worker": self.worker,
                    "fence": self.fences[number],
                    "phase": phase,
                },
            )

    def release(self, number: int, destination: str) -> None:
        fence = self.fences.pop(number, None)
        if fence is None:
            return
        outcome, reason = BIN_RELEASE.get(destination, ("abandoned", None))
        self._try(
            "release_work",
            {
                "target": f"issue:{number}",
                "worker": self.worker,
                "fence": fence,
                "outcome": outcome,
                **({"reason": reason} if reason else {}),
            },
        )


def mcp_caller(url: str, authorization: str) -> Callable[[str, dict], dict]:
    """Call one triage MCP server tool, synchronously, from a flow."""
    from fastmcp import Client
    from prefect.utilities.asyncutils import run_coro_as_sync

    async def call(tool: str, arguments: dict) -> dict:
        async with Client(url, auth=authorization.removeprefix("Bearer ")) as client:
            result = await client.call_tool(tool, arguments)
        return result.structured_content or {}

    return lambda tool, arguments: run_coro_as_sync(call(tool, arguments))


MODELS = Path(__file__).resolve().parents[1] / "workflows" / "stack-models.json"


def work_claims(config: object, owner: object) -> WorkClaims:
    """Claims for a triage run, on when `fastmcp_triage_config.work.mode` is on.

    The run reaches the triage MCP server with the same Horizon key the assess
    agent's memory tools use (stack-models.json `memory_server`).
    """
    settings = config.get("work") if isinstance(config, dict) else None
    if not isinstance(settings, dict) or settings.get("mode") != "on":
        return WorkClaims(None, str(owner))
    server = json.loads(MODELS.read_text()).get("memory_server") or {}
    try:
        authorization = load_block_value(server["header_block_id"])
    except (KeyError, ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
        logger.warning("Work claims are off: %s", type(error).__name__)
        return WorkClaims(None, str(owner))
    return WorkClaims(mcp_caller(server["url"], authorization), str(owner))


def load_block_value(block_id: str) -> str:
    from uuid import UUID

    from prefect.client.orchestration import get_client

    with get_client(sync_client=True) as client:
        document = client.read_block_document(UUID(block_id), include_secrets=True)
    value = (document.data or {}).get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("The memory server header block holds no value")
    return value
