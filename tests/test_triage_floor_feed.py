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
