from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from flows.agentic_work import (
    ACTIVITY,
    CLAIM,
    LEASE,
    Board,
    Repo,
    WorkClaims,
    holder_of,
    needs_retriage,
    rkey_of,
    work_claims,
)


class Pds:
    """Enough of a PDS: records with CIDs, swapRecord, sessions, expiring tokens."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], tuple[dict, str]] = {}
        self.logins = 0
        self.expire_next = 0
        self.before_put = None  # hook to race a write
        self.version = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "com.atproto.server.createSession":
            self.logins += 1
            return httpx.Response(
                200, json={"did": "did:x", "accessJwt": "a", "refreshJwt": "r"}
            )
        if method == "com.atproto.repo.getRecord":
            params = request.url.params
            found = self.records.get((params["collection"], params["rkey"]))
            if found is None:
                return httpx.Response(400, json={"error": "RecordNotFound"})
            return httpx.Response(200, json={"value": found[0], "cid": found[1]})
        if method == "com.atproto.repo.listRecords":
            collection = request.url.params["collection"]
            rows = [
                {"uri": f"at://did:x/{c}/{k}", "value": v, "cid": cid}
                for (c, k), (v, cid) in self.records.items()
                if c == collection
            ]
            rows.sort(key=lambda r: r["uri"], reverse=True)
            limit = int(request.url.params.get("limit", 50))
            return httpx.Response(200, json={"records": rows[:limit]})
        if method == "com.atproto.repo.putRecord":
            if self.expire_next:
                self.expire_next -= 1
                return httpx.Response(400, json={"error": "ExpiredToken"})
            body = json.loads(request.content)
            if self.before_put:
                hook, self.before_put = self.before_put, None
                hook()
            key = (body["collection"], body["rkey"])
            if "swapRecord" in body:
                current = self.records.get(key)
                if (current[1] if current else None) != body["swapRecord"]:
                    return httpx.Response(400, json={"error": "InvalidSwap"})
            self.version += 1
            cid = f"cid{self.version}"
            self.records[key] = (body["record"], cid)
            return httpx.Response(200, json={"uri": "at://x", "cid": cid})
        return httpx.Response(404)

    def log(self) -> list[dict]:
        return [
            value
            for (collection, rkey), (value, _) in sorted(self.records.items())
            if collection == ACTIVITY
        ]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 22, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def make_board(
    pds: Pds, store: dict | None = None, clock: Clock | None = None
) -> Board:
    store = {} if store is None else store
    repo = Repo(
        password=lambda: "pw",
        read_session=lambda: store.get("s"),
        write_session=lambda value: store.__setitem__("s", value),
        client=httpx.Client(transport=httpx.MockTransport(pds)),
    )
    return Board(repo, clock or Clock())


def test_first_claim_wins_and_the_holder_can_renew():
    pds = Pds()
    board = make_board(pds)
    first = board.claim("issue:6627", "session-a", "claude")
    assert first["claimed"] is True and first["fence"] == 1
    refused = board.claim("issue:6627", "session-b", "triage")
    assert refused["claimed"] is False
    assert refused["held_by"]["agent"] == "claude"
    assert refused["held_by"]["phase"] == "reading"
    again = board.claim("issue:6627", "session-a", "claude")
    assert again == {**first, "expires_at": again["expires_at"]}
    record = pds.records[(CLAIM, "issue-6627")][0]
    assert record["holder"] == holder_of("session-a")
    assert "session-a" not in json.dumps(list(pds.records.values()))
    assert [entry["kind"] for entry in pds.log()] == ["claimed"]


def test_progress_renews_and_logs_phase_changes_only():
    pds = Pds()
    clock = Clock()
    board = make_board(pds, clock=clock)
    fence = board.claim("issue:1", "w", "codex")["fence"]
    clock.now += timedelta(minutes=20)
    assert board.progress("issue:1", "w", fence, "editing")["ok"] is True
    assert board.progress("issue:1", "w", fence, "editing")["ok"] is True
    assert board.progress("issue:1", "w", fence, "pushing", pr=12)["ok"] is True
    claim = board.current("issue:1")
    assert claim.expires_at == clock.now + LEASE
    assert claim.phase == "pushing" and claim.pr == 12
    assert [e.get("phase") for e in pds.log()] == ["reading", "editing", "pushing"]
    assert board.progress("issue:1", "w", fence + 1, "testing")["ok"] is False
    assert board.progress("issue:1", "other", fence, "testing")["ok"] is False


def test_every_claim_ends_in_an_outcome():
    pds = Pds()
    board = make_board(pds)
    fence = board.claim("issue:5", "w", "claude")["fence"]
    with pytest.raises(ValueError):
        board.release("issue:5", "w", fence, "pr")
    with pytest.raises(ValueError):
        board.release("issue:5", "w", fence, "handed_back", reason="vibes")
    result = board.release(
        "issue:5", "w", fence, "handed_back", "decide_direction", used_related=[9, 3, 9]
    )
    assert result == {"ok": True, "outcome": "handed_back"}
    released = pds.log()[-1]
    assert released["kind"] == "released"
    assert released["reason"] == "decide_direction"
    assert released["usedRelated"] == [3, 9]
    # released work is free again, with a new fence
    assert board.claim("issue:5", "someone-else", "triage")["fence"] == fence + 1


def test_a_lapsed_lease_expires_and_fences_out_the_old_holder():
    pds = Pds()
    clock = Clock()
    board = make_board(pds, clock=clock)
    old = board.claim("pr:42", "slow", "claude")["fence"]
    clock.now += LEASE + timedelta(seconds=1)
    new = board.claim("pr:42", "fast", "codex")
    assert new["claimed"] is True and new["fence"] == old + 1
    assert [e["kind"] for e in pds.log()] == ["claimed", "expired", "claimed"]
    late = board.progress("pr:42", "slow", old, "pushing")
    assert late["ok"] is False
    assert board.current("pr:42").holder == holder_of("fast")


def test_a_racing_claim_loses_on_compare_and_swap():
    pds = Pds()
    board = make_board(pds)
    rival = make_board(pds)
    pds.before_put = lambda: rival.claim("issue:9", "rival", "codex")
    result = board.claim("issue:9", "me", "claude")
    assert result["claimed"] is False
    assert board.current("issue:9").holder == holder_of("rival")


def test_activity_pages_from_a_cursor_and_publishes_no_worker_ids():
    pds = Pds()
    clock = Clock()
    board = make_board(pds, clock=clock)
    entries, cursor = board.activity(None)
    assert entries == [] and cursor == ""
    fence = board.claim("issue:1", "w", "claude")["fence"]
    entries, cursor = board.activity(cursor)
    assert [e["kind"] for e in entries] == ["claimed"]
    board.progress("issue:1", "w", fence, "testing")
    board.claim("issue:2", "w2", "triage")
    clock.now += LEASE * 2  # issue:2 lapses; reading the log notices
    entries, cursor = board.activity(cursor)
    # newest claim records are read first, so issue:2 is marked expired first
    assert [(e["kind"], e["target"]) for e in entries] == [
        ("progress", "issue:1"),
        ("claimed", "issue:2"),
        ("expired", "issue:2"),
        ("expired", "issue:1"),
    ]
    assert board.activity(cursor) == ([], cursor)
    assert {e["holder"] for e in entries} == {holder_of("w"), holder_of("w2")}
    assert all("worker" not in e for e in entries)


def test_session_is_cached_and_refreshed_on_expiry():
    pds = Pds()
    store: dict = {}
    make_board(pds, store).claim("issue:1", "w", "claude")
    make_board(pds, store).claim("issue:2", "w", "claude")
    assert pds.logins == 1
    pds.expire_next = 1
    assert make_board(pds, store).claim("issue:3", "w", "claude")["claimed"]
    assert pds.logins == 2


def test_targets_are_a_fixed_shape():
    assert rkey_of("deploy:triage-stack") == "deploy-triage-stack"
    for bad in ("issue:0", "issue:abc", "deploy:prod", "security:1", "issue:1 x"):
        with pytest.raises(ValueError):
            rkey_of(bad)


def test_needs_retriage_skips_triage_handbacks():
    log = [
        {"kind": "claimed", "target": "issue:1", "agent": "claude"},
        {"kind": "expired", "target": "issue:1", "agent": "claude"},
        {
            "kind": "released",
            "target": "issue:2",
            "agent": "triage",
            "outcome": "handed_back",
        },
        {
            "kind": "released",
            "target": "issue:3",
            "agent": "codex",
            "outcome": "handed_back",
        },
        {"kind": "released", "target": "issue:4", "agent": "claude", "outcome": "pr"},
        {
            "kind": "released",
            "target": "issue:5",
            "agent": "claude",
            "outcome": "abandoned",
        },
        {"kind": "expired", "target": "pr:6", "agent": "claude"},
    ]
    assert needs_retriage(log) == [1, 3, 5]


# ---------- the tools, through a real MCP client ----------


@pytest.fixture
def server(monkeypatch):
    import memory_server

    pds = Pds()
    clock = Clock()
    monkeypatch.setenv("WORK_ATPROTO_PASSWORD", "pw")
    monkeypatch.setattr(memory_server, "_board", make_board(pds, clock=clock))
    items = {
        6627: {
            "number": 6627,
            "title": "t",
            "state": "open",
            "labels": [{"name": "bug"}],
        },
        6680: {"number": 6680, "state": "open", "pull_request": {}, "labels": []},
    }
    monkeypatch.setattr(memory_server, "_github_item", items.get)

    class NoIndex:
        def search(self, *args):
            return [{"number": 6600, "title": "older", "state": "closed"}]

    monkeypatch.setattr(memory_server, "index", NoIndex)
    return memory_server, pds


def call(module, steps: list[tuple[str, dict]]) -> list[dict]:
    from fastmcp import Client

    async def run():
        async with Client(module.mcp) as client:
            return [
                (
                    await client.call_tool(tool, args, raise_on_error=False)
                ).structured_content
                for tool, args in steps
            ]

    return asyncio.run(run())


def test_claim_report_release_through_the_tools(server):
    module, pds = server
    claimed, refused, progressed, active, released = call(
        module,
        [
            ("claim_work", {"target": "issue:6627", "worker": "s1", "agent": "claude"}),
            ("claim_work", {"target": "issue:6627", "worker": "s2", "agent": "triage"}),
            (
                "report_progress",
                {
                    "target": "issue:6627",
                    "worker": "s1",
                    "fence": 1,
                    "phase": "pushing",
                    "pr": 6680,
                },
            ),
            ("active_work", {}),
            (
                "release_work",
                {
                    "target": "issue:6627",
                    "worker": "s1",
                    "fence": 1,
                    "outcome": "pr",
                    "pr": 6680,
                    "used_related": [6600],
                },
            ),
        ],
    )
    assert claimed["claimed"] is True
    assert claimed["context"]["labels"] == ["bug"]
    assert claimed["context"]["related"][0]["number"] == 6600
    assert refused["claimed"] is False and refused["held_by"]["agent"] == "claude"
    assert progressed["ok"] is True
    assert active["result"][0]["target"] == "issue:6627"
    assert active["result"][0]["pr"] == 6680
    assert released == {"ok": True, "outcome": "pr"}
    assert [e["kind"] for e in pds.log()] == ["claimed", "progress", "released"]


def test_tools_refuse_what_isnt_public(server):
    module, pds = server
    unknown, wrong_kind, bad_pr, bad_target = call(
        module,
        [
            ("claim_work", {"target": "issue:999", "worker": "s"}),
            ("claim_work", {"target": "pr:6627", "worker": "s"}),
            (
                "report_progress",
                {
                    "target": "issue:6627",
                    "worker": "s",
                    "fence": 1,
                    "phase": "pushing",
                    "pr": 999,
                },
            ),
            ("claim_work", {"target": "security:1", "worker": "s"}),
        ],
    )
    assert unknown["claimed"] is False and wrong_kind["claimed"] is False
    assert bad_pr["ok"] is False
    assert bad_target is None  # rejected by the schema before the tool runs
    assert pds.records == {}


def test_tools_are_off_without_the_credential(monkeypatch):
    import memory_server

    monkeypatch.delenv("WORK_ATPROTO_PASSWORD", raising=False)
    monkeypatch.setattr(memory_server, "_board", None)
    (result,) = call(
        memory_server, [("claim_work", {"target": "issue:1", "worker": "s"})]
    )
    assert result["claimed"] is False


def test_work_activity_event_if_the_extension_is_available(server):
    module, pds = server
    if module.EventsExtension is None:
        pytest.skip("fastmcp without the events extension")
    from fastmcp.experimental.events import EventPoll

    first = module.work_activity(EventPoll())
    call(module, [("claim_work", {"target": "issue:6627", "worker": "s1"})])
    batch = module.work_activity(
        EventPoll(cursor=first.cursor, arguments={"kinds": ["claimed"]})
    )
    assert [e.data["target"] for e in batch.events] == ["issue:6627"]
    assert batch.events[0].event_id == batch.cursor


# ---------- the triage workflow's side ----------


def test_triage_claims_steps_aside_and_releases_with_outcome_names():
    calls = []

    def fake(tool, arguments):
        calls.append((tool, arguments))
        if tool == "claim_work":
            if arguments["target"] == "issue:2":
                return {"claimed": False, "held_by": {"agent": "claude"}}
            return {"claimed": True, "fence": 3}
        return {"ok": True}

    claims = WorkClaims(fake, "run-1")
    assert claims.claim(1) is None
    assert claims.claim(2) == {"agent": "claude"}
    claims.progress(1, "assessing")
    claims.progress(2, "assessing")  # not ours: no call
    claims.release(1, "existing_review")
    assert [tool for tool, _ in calls] == [
        "claim_work",
        "claim_work",
        "report_progress",
        "release_work",
    ]
    assert calls[-1][1] == {
        "target": "issue:1",
        "worker": "run-1",
        "fence": 3,
        "outcome": "handed_back",
        "reason": "review_linked_pr",
    }


def test_triage_carries_on_when_the_server_is_down():
    def down(tool, arguments):
        raise httpx.ConnectError("nope")

    claims = WorkClaims(down, "run")
    assert claims.claim(1) is None
    assert claims.claim(2) is None
    assert work_claims({}, "owner")._call is None
    assert work_claims({"work": {"mode": "off"}}, "owner")._call is None


def test_controller_skips_items_another_agent_holds(monkeypatch):
    import flows.agentic_stack_controller as controller
    from flows.agentic_stack_policy import Batch
    from flows.agentic_stack_runtime import AgentPlan, Runtime

    now = datetime.now(UTC)
    batch = Batch.model_validate(
        {
            "id": "b",
            "started_at": now,
            "expires_at": now + timedelta(hours=1),
            "groups": [
                {
                    "name": "g",
                    "rationale": "r",
                    "branch": "main",
                    "base_sha": "a" * 40,
                    "items": [{"issue_number": 7}, {"issue_number": 8}],
                }
            ],
        }
    )
    runtime = Runtime(
        plans={
            r: AgentPlan(flow_id=uuid4(), version_id=uuid4())
            for r in ("assess", "reproduce")
        },
        source_sha="b" * 40,
        triage_plan=AgentPlan(flow_id=uuid4(), version_id=uuid4()),
        triage_verify_id=uuid4(),
        triage_finalize_id=uuid4(),
    )
    evidence = {"base_sha": "a" * 40}
    collected = []
    for name, value in {
        "batch_owner": lambda *_: uuid4(),
        "saved": lambda *_: None,
        "record": lambda *_: None,
        "owner_alive": lambda *_: None,
        "skills": lambda _: {
            "existing_instructions": {},
            "assess_instructions": {},
            "reproduce_instructions": {},
        },
        "collect": lambda number, _: collected.append(number) or evidence,
        "route_intake": lambda _: {"route": "review_existing"},
        "assessment_context": lambda _: {},
        "run_native_triage": lambda owner, admission: (
            "existing_review",
            {"issue_number": admission.requested.issue_number},
            [],
            evidence,
        ),
        "usage_report": lambda _: {},
        "revalidate": lambda result, *_: result,
    }.items():
        monkeypatch.setattr(controller, name, value)

    released = []

    def fake(tool, arguments):
        if tool == "claim_work":
            if arguments["target"] == "issue:7":
                return {"claimed": False, "held_by": {"agent": "claude"}}
            return {"claimed": True, "fence": 1}
        if tool == "release_work":
            released.append(arguments)
        return {"ok": True}

    result = controller.run_batch(batch, runtime, WorkClaims(fake, "run"))
    assert collected == [8]
    (skipped,) = result["bins"]["decisions"]
    assert skipped["issue_number"] == 7
    assert skipped["route"]["route"] == "claimed"
    assert [r["target"] for r in released] == ["issue:8"]


def test_work_activity_over_the_events_protocol(server):
    module, pds = server
    if module.EventsExtension is None:
        pytest.skip("fastmcp without the events extension")
    from fastmcp import Client
    from fastmcp.experimental.events import (
        EventsClientExtension,
        list_events,
        poll_events_mcp,
    )

    async def run():
        async with Client(module.mcp, extensions=[EventsClientExtension()]) as client:
            listed = await list_events(client)
            start = await poll_events_mcp(client, "work.activity")
            await client.call_tool(
                "claim_work", {"target": "issue:6627", "worker": "s1", "agent": "codex"}
            )
            later = await poll_events_mcp(
                client,
                "work.activity",
                arguments={"target": "issue:6627"},
                cursor=start.cursor,
            )
        return listed, later

    listed, later = asyncio.run(run())
    assert "work.activity" in [event.name for event in listed]
    assert [(e.data["kind"], e.data["agent"]) for e in later.events] == [
        ("claimed", "codex")
    ]
