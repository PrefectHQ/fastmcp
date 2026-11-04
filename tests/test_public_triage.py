"""Public analytics preserve uncertainty and do not manufacture review verdicts."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)


@pytest.fixture
def triage():
    spec = importlib.util.spec_from_file_location(
        "public_triage", Path(__file__).parents[1] / "scripts/public_triage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def item(number=1, **overrides):
    return {
        "number": number,
        "title": "Fix supported behavior",
        "created_at": "2026-10-01T00:00:00Z",
        "state": "open",
        "pull_request": {},
        "draft": False,
        "author_association": "NONE",
        "user": {"login": "contributor", "type": "User"},
        **overrides,
    }


def pr(**overrides):
    return {
        **item(),
        "head": {"sha": "a" * 40},
        "base": {"sha": "b" * 40, "ref": "main"},
        "updated_at": "2026-11-03T00:00:00Z",
        **overrides,
    }


def activity(login, when, *, bot=False, association="MEMBER", **fields):
    return {
        "user": {"login": login, "type": "Bot" if bot else "User"},
        "author_association": association,
        "created_at": when,
        **fields,
    }


def test_discussion_after_first_page_and_historical_review(triage, monkeypatch):
    # Paginated REST pages include a maintainer after 100 earlier comments.
    comments = [activity("contributor", "2026-10-02T00:00:00Z")] * 100
    last = activity("maintainer", "2026-11-02T00:00:00Z")
    review = activity(
        "fastmcp-triage-reviewer[bot]",
        "2026-10-03T00:00:00Z",
        bot=True,
        commit_id="c" * 40,
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        value = [[*comments], [last]] if "comments" in command[2] else [[review]]
        if "--paginate" not in command:
            value = {"commit": {"sha": "b" * 40}} if "branches/" in command[2] else pr()
        return SimpleNamespace(stdout=json.dumps(value))

    monkeypatch.setattr(triage.subprocess, "run", run)
    report = triage.build([item()], NOW)
    lead = report["review_leads"][0]
    assert lead["activity"]["comments"] == 101
    assert lead["activity"]["last_maintainer_account_activity_at"] == last["created_at"]
    assert lead["activity"]["triage_app_review_seen"] is True
    assert lead["activity"]["triage_app_review_on_snapshot_revision"] is False
    assert lead["head_sha"] == "a" * 40
    assert all(
        "--slurp" in command
        for command in calls
        if "comments" in command[2] or "reviews" in command[2]
    )


def test_failed_activity_is_unknown_not_unanswered(triage, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 20)

    monkeypatch.setattr(triage, "github", failed)
    report = triage.build([item()], NOW)
    lead = report["review_leads"][0]
    assert lead["activity"] == {"status": "unavailable"}
    assert "first_maintainer_account_response_at" not in lead["activity"]
    assert "collection_gap" in lead
    assert report["open_age_buckets"]["prs"]["30_90d"] == 1


def test_bot_and_author_are_not_maintainer_response(triage, monkeypatch):
    comments = [
        activity("contributor", "2026-10-02T00:00:00Z"),
        activity("bot", "2026-10-03T00:00:00Z", bot=True),
    ]
    monkeypatch.setattr(
        triage,
        "github",
        lambda path, **kw: pr() if not kw else comments if "comments" in path else [],
    )
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert lead["activity"]["status"] == "collected"
    assert lead["activity"]["first_maintainer_account_response_at"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"draft": True},
        {"title": "[DNM] Fix"},
        {"body": "DO NOT MERGE"},
        {"labels": [{"name": "dont-merge"}]},
        {"author_association": "MEMBER"},
        {"user": {"login": "bot", "type": "Bot"}},
    ],
)
def test_holds_drafts_bots_and_maintainers_are_not_leads(triage, monkeypatch, change):
    monkeypatch.setattr(
        triage, "github", lambda *a, **kw: pytest.fail("must not inspect excluded PR")
    )
    report = triage.build([item(**change)], NOW)
    assert not report["review_leads"]
    assert sum(report["open_age_buckets"]["prs"].values()) == 1


def test_hold_after_inventory_prevents_nomination(triage, monkeypatch):
    monkeypatch.setattr(triage, "github", lambda *a, **kw: pr(title="[DRAFT] held"))
    report = triage.build([item()], NOW)
    assert report["selection"]["eligible_in_inventory"] == 1
    assert not report["review_leads"]


def test_age_boundaries_and_empty_success_are_explicit(triage):
    dates = ["2026-11-03", "2026-10-27", "2026-10-04", "2026-08-05"]
    items = [
        item(i, pull_request=None, created_at=d + "T18:00:00Z")
        for i, d in enumerate(dates)
    ]
    report = triage.build(items, NOW)
    assert report["open_age_buckets"]["issues"] == {
        "lt_7d": 1,
        "7_30d": 1,
        "30_90d": 1,
        "gte_90d": 1,
    }
    empty = triage.build([], NOW)
    assert empty["inventory_items"] == 0
    assert empty["collection_started_at"] == NOW.isoformat()
    assert empty["collection_finished_at"]
    assert empty["selection"]["returned"] == 0


def test_bounded_queue_keeps_counts_and_does_not_export_bodies(triage, monkeypatch):
    calls = []

    def inspect(number, now):
        calls.append(number)
        return {"activity": {"status": "unavailable"}}

    monkeypatch.setattr(triage, "inspect_pr", inspect)
    report = triage.build(
        [item(2, body="unpublished-sentinel input"), item(1)], NOW, limit=1
    )
    assert calls == [1]
    assert report["selection"]["eligible_in_inventory"] == 2
    assert sum(report["open_age_buckets"]["prs"].values()) == 2
    assert "unpublished-sentinel" not in json.dumps(report)


def test_counts_use_collection_start_like_backlog_chart(triage):
    report = triage.build(
        [
            item(
                1, pull_request=None, state="closed", closed_at="2026-11-03T18:01:00Z"
            ),
            item(
                2, pull_request=None, state="closed", closed_at="2026-11-03T17:59:00Z"
            ),
            item(3, pull_request=None, created_at="2026-11-03T18:01:00Z"),
        ],
        NOW,
    )
    assert sum(report["open_age_buckets"]["issues"].values()) == 1


def test_discussion_failure_preserves_successful_head_observation(triage, monkeypatch):
    def fetch(path, **kwargs):
        if kwargs:
            raise subprocess.TimeoutExpired("gh", 20)
        return pr()

    monkeypatch.setattr(triage, "github", fetch)
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert lead["head_sha"] == "a" * 40
    assert lead["pr_collected_at"]
    assert lead["activity"] == {"status": "unavailable"}
    assert "collection_gap" in lead


@pytest.mark.parametrize(
    "failure,expected_kind,status,expected_calls",
    [
        (
            subprocess.TimeoutExpired("gh", 15, stderr="secret-sentinel"),
            "timeout",
            None,
            2,
        ),
        (
            subprocess.CalledProcessError(1, "gh", stderr="secret-sentinel (HTTP 503)"),
            "http_error",
            503,
            2,
        ),
        (
            subprocess.CalledProcessError(1, "gh", stderr="secret-sentinel (HTTP 403)"),
            "http_error",
            403,
            1,
        ),
        (
            subprocess.CalledProcessError(
                1,
                "gh",
                stderr="invalid character 'S' looking for beginning of value: secret-sentinel",
            ),
            "invalid_json",
            None,
            2,
        ),
    ],
)
def test_sanitized_diagnostics_and_bounded_retry(
    triage, monkeypatch, failure, expected_kind, status, expected_calls
):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise failure

    monkeypatch.setattr(triage.subprocess, "run", run)
    monkeypatch.setattr(triage.time, "sleep", lambda seconds: None)
    report = triage.build([item()], NOW)
    gap = report["review_leads"][0]["collection_diagnostics"][0]
    assert gap == {
        "stage": "pr_metadata",
        "kind": expected_kind,
        "attempts": expected_calls,
        "http_status": status,
    }
    assert len(calls) == expected_calls
    assert "secret-sentinel" not in json.dumps(report)
    assert report["review_leads"][0]["activity"]["status"] == "unavailable"


def test_transient_non_json_response_recovers_without_retaining_body(
    triage, monkeypatch
):
    results = iter(
        [
            SimpleNamespace(stdout="Server Error secret-sentinel"),
            SimpleNamespace(stdout=json.dumps(pr())),
        ]
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(triage.subprocess, "run", run)
    monkeypatch.setattr(triage.time, "sleep", lambda seconds: None)
    assert triage.github("pulls/1")["head"]["sha"] == "a" * 40
    assert len(calls) == 2


def marked_review(*, head="a" * 40, baseline="b" * 40, number=1, **overrides):
    return (
        activity(
            "fastmcp-triage-reviewer[bot]",
            "2026-10-03T00:00:00Z",
            bot=True,
            id=7,
            state="COMMENTED",
            commit_id=head,
            body=f"<!-- fastmcp-triage-review:v1 pr:{number} head:{head} base:{baseline} -->\nPublic finding",
        )
        | overrides
    )


@pytest.mark.parametrize(
    "review,expected",
    [
        (marked_review(), True),
        (marked_review(baseline="c" * 40), False),
        (marked_review(head="c" * 40), False),
        (marked_review(number=2), False),
        (marked_review(commit_id="c" * 40), False),
        (marked_review(body="unbound review"), False),
        (marked_review(state="DISMISSED"), False),
    ],
)
def test_review_provenance_requires_pr_head_and_current_target_baseline(
    triage, monkeypatch, review, expected
):
    def fetch(path, **kwargs):
        if path.startswith("branches/"):
            return {"commit": {"sha": "b" * 40}}
        if path.endswith("/reviews?per_page=100"):
            return [review]
        return [] if kwargs else pr(base={"sha": "d" * 40, "ref": "main"})

    monkeypatch.setattr(triage, "github", fetch)
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert (
        lead["base_sha"] == "d" * 40
    )  # PR metadata isn't the authoritative target tip.
    assert lead["baseline_sha"] == "b" * 40
    assert lead["activity"]["triage_app_review_on_snapshot_revision"] is expected
    assert lead["activity"]["triage_app_review_seen"] is True


def test_missing_baseline_stays_unknown_while_discussion_is_retained(
    triage, monkeypatch
):
    def fetch(path, **kwargs):
        if path.startswith("branches/"):
            raise triage.GitHubReadError("http_error", 1, 403)
        if "/reviews?" in path:
            return [marked_review()]
        return [] if kwargs else pr()

    monkeypatch.setattr(triage, "github", fetch)
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert lead["head_sha"] == "a" * 40
    assert lead["baseline_sha"] is None
    assert lead["activity"]["status"] == "collected"
    assert lead["activity"]["triage_app_review_on_snapshot_revision"] is None
    assert lead["collection_diagnostics"] == [
        {
            "stage": "target_baseline",
            "kind": "http_error",
            "attempts": 1,
            "http_status": 403,
        }
    ]


def test_untrusted_account_cannot_supply_revision_marker(triage, monkeypatch):
    review = marked_review(
        user={"login": "fastmcp-triage-reviewer[bot]", "type": "User"}
    )
    monkeypatch.setattr(
        triage,
        "github",
        lambda path, **kw: (
            {"commit": {"sha": "b" * 40}}
            if path.startswith("branches/")
            else [review]
            if "/reviews?" in path
            else []
            if kw
            else pr()
        ),
    )
    activity_result = triage.build([item()], NOW)["review_leads"][0]["activity"]
    assert activity_result["triage_app_review_seen"] is False
    assert activity_result["triage_app_review_bindings"] == []


def test_optional_triage_failure_cannot_block_backlog_publication():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/backlog-status.yml").read_text()
    )
    jobs = workflow["jobs"]
    assert jobs["publish"]["needs"] == "build"
    assert jobs["publish-triage"]["needs"] == "triage"
    assert "needs" not in jobs["build"] and "needs" not in jobs["triage"]
    maintenance = yaml.safe_load(
        (
            Path(__file__).parents[1] / ".github/workflows/maintenance-status.yml"
        ).read_text()
    )
    for publisher in (
        jobs["publish"],
        jobs["publish-triage"],
        maintenance["jobs"]["publish"],
    ):
        assert publisher["concurrency"] == {
            "group": "maintenance-status-publish",
            "cancel-in-progress": False,
            "queue": "max",
        }
    commands = "\n".join(step.get("run", "") for step in jobs["build"]["steps"])
    assert "--triage-output" not in commands and "public_triage.py" not in commands
    assert (
        "pull_request" in jobs["publish"]["if"]
        and "pull_request" in jobs["publish-triage"]["if"]
    )


def test_malformed_discussion_retains_metadata_and_stage(triage, monkeypatch):
    def fetch(path, **kwargs):
        if path.startswith("branches/"):
            return {"commit": {"sha": "b" * 40}}
        return ["secret-sentinel"] if kwargs else pr()

    monkeypatch.setattr(triage, "github", fetch)
    report = triage.build([item()], NOW)
    lead = report["review_leads"][0]
    assert lead["head_sha"] == "a" * 40
    assert lead["activity"]["status"] == "unavailable"
    assert lead["collection_diagnostics"] == [
        {
            "stage": "discussion",
            "kind": "invalid_response",
            "attempts": 1,
            "http_status": None,
        }
    ]
    assert report["lead_collection_status"] == "partial"
    assert "secret-sentinel" not in json.dumps(report)


def test_standalone_cli_generates_provenance_without_cloud_access(tmp_path):
    # Intercept only the external gh boundary in the child interpreter. A fake
    # shebang executable on PATH cannot run on Windows.
    wrapper = tmp_path / "run_collector.py"
    wrapper.write_text(
        "import json, runpy, subprocess, sys\nfrom pathlib import Path\n"
        "original_run = subprocess.run\n"
        "def fake_run(command, *args, **kwargs):\n"
        "    if command[0] != 'gh':\n"
        "        return original_run(command, *args, **kwargs)\n"
        "    endpoint = command[2]\n"
        "    if 'per_page=1&' in endpoint: value = 1\n"
        f"    elif '/issues?' in endpoint: value = {item()!r}\n"
        f"    elif '/branches/' in endpoint: value = {{'commit': {{'sha': {'b' * 40!r}}}}}\n"
        "    elif '--slurp' in command: value = [[]]\n"
        f"    else: value = {pr()!r}\n"
        "    return subprocess.CompletedProcess(command, 0, json.dumps(value), '')\n"
        "subprocess.run = fake_run\n"
        "sys.argv = sys.argv[1:]\n"
        "sys.path.insert(0, str(Path(sys.argv[0]).parent))\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    output = tmp_path / "triage.json"
    root = Path(__file__).parents[1]
    subprocess.run(
        [
            sys.executable,
            str(wrapper),
            str(root / "scripts/public_triage.py"),
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(output.read_text())
    assert len(report["source_sha"]) == 40
    assert report["inventory_items"] == 1
    assert report["lead_collection_status"] == "collected"
    assert report["review_leads"][0]["baseline_sha"] == "b" * 40
    assert report["review_leads"][0]["activity"]["status"] == "collected"
