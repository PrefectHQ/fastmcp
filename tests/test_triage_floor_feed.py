import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "triage_floor_feed", Path(__file__).parents[1] / "scripts" / "triage_floor_feed.py"
)
assert spec and spec.loader
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=UTC)


def test_merged_pull_request_is_merged():
    payload = {
        "action": "closed",
        "pull_request": {"number": 7, "title": "fix", "merged": True, "html_url": "u"},
        "sender": {"login": "jane", "type": "User"},
    }
    event = feed.to_event("pull_request_target", payload, NOW)
    assert event["kind"] == "merged"
    assert event["pr"] is True
    assert event["bot"] is False


def test_gate_label_is_gated():
    payload = {
        "action": "labeled",
        "label": {"name": feed.GATE_LABEL},
        "pull_request": {"number": 8, "title": "x"},
        "sender": {"login": "marvin[bot]", "type": "Bot"},
    }
    event = feed.to_event("pull_request_target", payload, NOW)
    assert event["kind"] == "gated"
    assert event["bot"] is True


def test_bot_comment_and_release():
    comment = {
        "action": "created",
        "issue": {"number": 3, "title": "q", "pull_request": {}},
        "sender": {"login": "triage[bot]", "type": "Bot"},
    }
    assert feed.to_event("issue_comment", comment, NOW)["kind"] == "botcomment"
    release = {
        "action": "published",
        "release": {"tag_name": "v4.2.4", "html_url": "r"},
        "sender": {"login": "nate"},
    }
    event = feed.to_event("release", release, NOW)
    assert event["kind"] == "release"
    assert "number" not in event


def test_uninteresting_actions_are_skipped():
    assert (
        feed.to_event("issues", {"action": "edited", "issue": {"number": 1}}, NOW)
        is None
    )
    assert feed.to_event("schedule", {}, NOW) is None


def test_fresh_cached_session_skips_login(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "did": "did:plc:x",
                "accessJwt": "a",
                "refreshJwt": "r",
                "at": (NOW - timedelta(minutes=5)).isoformat(),
            }
        )
    )
    monkeypatch.setattr(
        feed,
        "xrpc",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")),
    )
    assert feed.session("https://pds", str(path), NOW) == ("did:plc:x", "a")


def test_stale_session_refreshes(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "did": "did:plc:x",
                "accessJwt": "a",
                "refreshJwt": "r",
                "at": (NOW - timedelta(hours=3)).isoformat(),
            }
        )
    )
    calls = []

    def fake(pds, method, body, token=None):
        calls.append(method)
        return {"did": "did:plc:x", "accessJwt": "a2", "refreshJwt": "r2"}

    monkeypatch.setattr(feed, "xrpc", fake)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert feed.session("https://pds", str(path), NOW) == ("did:plc:x", "a2")
    assert calls == ["com.atproto.server.refreshSession"]
    assert json.loads(path.read_text())["refreshJwt"] == "r2"


def test_snapshot_gives_way_to_a_newer_one(monkeypatch):
    newer = {"cid": "c1", "value": {"updatedAt": "2026-11-22T06:00:05.000Z"}}
    monkeypatch.setattr(feed, "xrpc_get", lambda *a, **k: newer)
    monkeypatch.setattr(
        feed,
        "xrpc",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not write")),
    )
    assert feed.put_snapshot(
        "https://pds", "did:plc:x", "t", {"updatedAt": "2026-11-22T06:00:00.000Z"}
    ) == (False, None)


def test_snapshot_swaps_on_the_cid_it_read(monkeypatch):
    older = {"cid": "c1", "value": {"updatedAt": "2026-11-22T05:00:00.000Z"}}
    bodies = []
    monkeypatch.setattr(feed, "xrpc_get", lambda *a, **k: older)
    monkeypatch.setattr(
        feed, "xrpc", lambda pds, method, body, token=None: bodies.append(body) or {}
    )
    assert feed.put_snapshot(
        "https://pds", "did:plc:x", "t", {"updatedAt": "2026-11-22T06:00:00.000Z"}
    ) == (True, older["value"])
    assert bodies[0]["swapRecord"] == "c1"


def test_events_without_a_snapshot_publish_nothing(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setattr("sys.argv", ["feed", "--dry-run"])
    assert feed.main() == 0
    assert "nothing to publish" in capsys.readouterr().out


def row(n, *, pr=True, labels=(), draft=False, bot=False, days=10, idle=1):
    return {
        "n": n,
        "pr": pr,
        "labels": list(labels),
        "draft": draft,
        "bot": bot,
        "created": (NOW - timedelta(days=days)).isoformat(),
        "updated": (NOW - timedelta(days=idle)).isoformat(),
    }


def test_day_counts_match_the_floors_buckets():
    items = [
        row(1, pr=False, idle=40),
        row(2, labels=["missing-issue-link"], bot=True),
        row(3, bot=True),
        row(4, draft=True),
        row(5, days=10),
        row(6, days=30),
        row(7, days=50),
    ]
    counts = feed.day_counts(items, NOW)
    assert counts == {
        "issues": 1,
        "ready": 3,
        "gated": 1,
        "drafts": 1,
        "bots": 1,
        "readyWaitDays": 30,
        "stalePermille": 143,
    }


def test_flow_counts_moves_between_stages():
    before = [row(1, labels=["missing-issue-link"]), row(2), row(3, pr=False)]
    after = [row(1), row(4, labels=["missing-issue-link"]), row(3, pr=False)]
    assert feed.flow(before, after) == {
        "issues": {"in": 0, "out": 0},
        "gate": {"in": 1, "out": 1},
        "ready": {"in": 1, "out": 1},
    }


def test_day_record_adds_this_runs_moves_to_the_days(monkeypatch):
    stored = {
        "cid": "c1",
        "value": {"flow": {"ready": {"in": 2, "out": 5}}},
    }
    bodies = []
    monkeypatch.setattr(feed, "xrpc_get", lambda *a, **k: stored)
    monkeypatch.setattr(
        feed, "xrpc", lambda pds, method, body, token=None: bodies.append(body) or {}
    )
    moved = {"ready": {"in": 1, "out": 0}, "gate": {"in": 0, "out": 1}}
    feed.put_day("https://pds", "did:plc:x", "t", {"day": "2026-11-22"}, moved)
    assert bodies[0]["rkey"] == "2026-11-22"
    assert bodies[0]["swapRecord"] == "c1"
    assert bodies[0]["record"]["flow"] == {
        "issues": {"in": 0, "out": 0},
        "gate": {"in": 0, "out": 1},
        "ready": {"in": 3, "out": 5},
    }


def test_comment_carries_an_excerpt_and_the_items_author():
    payload = {
        "action": "created",
        "issue": {"number": 5, "title": "t", "user": {"login": "reporter"}},
        "comment": {"body": "Thanks!\n\nCan you try " + "x" * 300},
        "sender": {"login": "nate", "type": "User"},
    }
    event = feed.to_event("issue_comment", payload, NOW)
    assert event["author"] == "reporter"
    assert event["excerpt"].startswith("Thanks! Can you try")
    assert len(event["excerpt"]) == 160


def test_new_commits_are_pushed_not_updated():
    payload = {
        "action": "synchronize",
        "pull_request": {"number": 7, "title": "fix"},
        "sender": {"login": "jane", "type": "User"},
    }
    assert feed.to_event("pull_request_target", payload, NOW)["kind"] == "pushed"


def test_an_older_run_keeps_the_newer_counts(monkeypatch):
    stored = {
        "cid": "c1",
        "value": {
            "day": "2026-11-22",
            "ready": 9,
            "updatedAt": "2026-11-22T06:00:00.000Z",
        },
    }
    bodies = []
    monkeypatch.setattr(feed, "xrpc_get", lambda *a, **k: stored)
    monkeypatch.setattr(
        feed, "xrpc", lambda pds, method, body, token=None: bodies.append(body) or {}
    )
    older = {"day": "2026-11-22", "ready": 4, "updatedAt": "2026-11-22T05:00:00.000Z"}
    feed.put_day("https://pds", "did:plc:x", "t", older, {"ready": {"in": 1, "out": 0}})
    assert bodies[0]["record"]["ready"] == 9
    assert bodies[0]["record"]["flow"]["ready"] == {"in": 1, "out": 0}
