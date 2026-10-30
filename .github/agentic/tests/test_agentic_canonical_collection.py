import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import unquote

import pytest

from tests.test_agentic_evidence_collection import ROOT, pr

PROXY = """\
import json, sys
from urllib.parse import parse_qsl, quote
from urllib.request import urlopen
args = iter(sys.argv[2:]); fields = {}; endpoint = None
for arg in args:
    if arg == 'api': continue
    if arg == '-f':
        k, _, v = next(args).partition('='); fields[k] = v
    elif arg.startswith('-'): next(args)
    else: endpoint = arg
path, _, query = endpoint.partition('?')
if dict(parse_qsl(query)).get('page', '1') != '1':
    print('[]'); sys.exit(0)
key = 'search:' + fields['q'] if path == 'search/issues' else path
with urlopen(sys.argv[1] + '/' + quote(key, safe='')) as response:
    sys.stdout.write(response.read().decode())
"""


@pytest.fixture
def collector(tmp_path):
    routes, calls = {}, []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            key = unquote(self.path[1:])
            calls.append(key)
            body = routes.get(key, {"message": "Unrouted " + key})
            if callable(body):
                body = body(calls.count(key))
            self.send_response(200 if key in routes else 404)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = tmp_path / "proxy.py"
    proxy.write_text(PROXY)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{proxy}" "http://127.0.0.1:{server.server_port}" "$@"\n'
    )
    gh.chmod(0o755)

    def run(mode="route"):
        return subprocess.run(
            [
                "bash",
                str(ROOT / "scripts/collect_fastmcp_stack_evidence.sh"),
                "2026",
                "main",
                mode,
            ],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            timeout=60,
        )

    repo = "repos/PrefectHQ/fastmcp"
    routes.update(
        {
            f"{repo}/issues/2026": {
                "number": 2026,
                "title": "Schema reference pruning",
                "body": "",
                "state": "open",
                "html_url": "https://github.com/PrefectHQ/fastmcp/issues/2026",
                "updated_at": "2026-10-21T00:00:00Z",
                "user": {"id": 2, "login": "reporter", "type": "User"},
                "author_association": "NONE",
                "assignees": [],
                "labels": [],
            },
            f"{repo}/issues/2026/comments": [],
            f"{repo}/issues/2026/timeline": [],
            f"{repo}/labels": [],
            f"{repo}/stacks": [],
            f"{repo}/commits/main": {"sha": "b" * 40},
            "search:repo:PrefectHQ/fastmcp is:pr 2026 in:body": {
                "total_count": 1,
                "incomplete_results": False,
                "items": [{"number": 9001, "body": "Fixes #2026"}],
            },
            'search:repo:PrefectHQ/fastmcp is:pr "reference" "pruning" "Schema"': {
                "total_count": 0,
                "incomplete_results": False,
                "items": [],
            },
        }
    )

    def add(number, body="Fixes #2026", comments=None, **changes):
        value = {**pr(number, body), **changes}
        routes.update(
            {
                f"{repo}/pulls/{number}": value,
                f"{repo}/issues/{number}": {
                    "number": number,
                    "html_url": value["html_url"],
                    "pull_request": {"url": "api"},
                },
                f"{repo}/issues/{number}/comments": comments or [],
                f"{repo}/issues/{number}/timeline": [],
                f"{repo}/pulls/{number}/reviews": [],
                f"{repo}/pulls/{number}/comments": [],
                f"{repo}/commits/{value['head']['sha']}/check-runs": {"check_runs": []},
                f"{repo}/commits/{value['head']['sha']}/statuses": [],
            }
        )
        return value

    add(9001)
    yield run, add, routes, calls
    server.shutdown()
    thread.join()


def comment(body, association="MEMBER", kind="User", ident=1):
    return {
        "id": ident,
        "body": body,
        "html_url": f"https://github.com/PrefectHQ/fastmcp/pull/9001#issuecomment-{ident}",
        "author_association": association,
        "user": {"id": 1, "login": "maintainer", "type": kind},
    }


@pytest.mark.parametrize("mode", ["route", "publication"])
def test_real_collector_follows_historical_canonical_once_with_full_discussion(
    collector, mode
):
    run, add, _, calls = collector
    add(
        9001,
        comments=[
            comment(
                "We’re continuing #5588 as the canonical fix, following the earlier decision on #6033."
            ),
            comment(
                "#5588 already fixes the same pruning, so closing this as a duplicate.",
                ident=2,
            ),
        ],
    )
    add(
        5588,
        body="References an older issue",
        comments=[comment("Superseded by #7777")],
        state="closed",
        merged=False,
    )
    result = run(mode)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert [value["number"] for value in snapshot["prs"]] == [5588, 9001]
    canonical, source = snapshot["prs"]
    assert canonical["state"] == "closed" and canonical["merged"] is False
    assert (
        "reviews" in canonical
        and "checks" in canonical
        and "discussion_collected" not in canonical
    )
    assert {source["comment_id"] for source in canonical["canonical_references"]} == {
        1,
        2,
    }
    assert source["comments"][0]["author_association"] == "MEMBER"
    assert calls.count("repos/PrefectHQ/fastmcp/issues/5588") == 1
    assert not any("6033" in path or "7777" in path for path in calls)


@pytest.mark.parametrize(
    "body,association,kind",
    [
        ("Canonical #5588", "NONE", "User"),
        ("Canonical #5588", "MEMBER", "Bot"),
        ("Canonical https://github.com/other/repo/pull/5588", "MEMBER", "User"),
        (
            "Canonical [#5588](https://github.com/other/repo/pull/5588)",
            "MEMBER",
            "User",
        ),
        ("Use other/repo#5588 as the canonical fix", "MEMBER", "User"),
        ("Some unrelated discussion mentions #5588", "MEMBER", "User"),
    ],
)
def test_untrusted_or_unrelated_discussion_cannot_amplify_collection(
    collector, body, association, kind
):
    run, add, _, calls = collector
    add(9001, comments=[comment(body, association, kind)])
    result = run()
    assert result.returncode == 0, result.stderr
    assert [value["number"] for value in json.loads(result.stdout)["prs"]] == [9001]
    assert not any("5588" in path for path in calls)


def test_canonical_fanout_bound_fails_before_fetching_any_references(collector):
    run, add, _, calls = collector
    add(
        9001,
        comments=[
            comment(f"Canonical #{number}", ident=number) for number in range(20, 24)
        ],
    )
    result = run()
    assert result.returncode != 0 and "three canonical" in result.stderr
    assert not any(
        path.endswith(tuple(f"/{number}" for number in range(20, 24))) for path in calls
    )


def test_local_issue_reference_does_not_become_a_pull_request(collector):
    run, add, routes, calls = collector
    add(9001, comments=[comment("Canonical #5588")])
    routes["repos/PrefectHQ/fastmcp/issues/5588"] = {
        "number": 5588,
        "html_url": "https://github.com/PrefectHQ/fastmcp/issues/5588",
    }
    result = run()
    assert result.returncode == 0, result.stderr
    assert [value["number"] for value in json.loads(result.stdout)["prs"]] == [9001]
    assert "repos/PrefectHQ/fastmcp/pulls/5588" not in calls


@pytest.mark.parametrize("number", [9001, 5588])
def test_source_or_canonical_changes_during_enrichment_fail_freshness(
    collector, number
):
    run, add, routes, _ = collector
    add(9001, comments=[comment("Superseded by #5588")])
    value = add(5588, body="Original contribution")
    if number == 9001:
        value = routes["repos/PrefectHQ/fastmcp/pulls/9001"]
    routes[f"repos/PrefectHQ/fastmcp/pulls/{number}"] = (
        lambda count: value
        if count == 1
        else {**value, "updated_at": "2026-10-28T00:00:00Z"}
    )
    result = run()
    assert result.returncode != 0 and "Contribution changed" in result.stderr


def test_collected_canonical_can_be_selected_for_existing_contribution_verification(
    collector,
):
    from flows.agentic_existing_review import verification_specs
    from flows.agentic_intake import route_intake
    from flows.agentic_stack_policy import Group

    run, add, _, _ = collector
    add(
        9001,
        comments=[comment("Continuing #5588 as the canonical fix.")],
        state="closed",
    )
    add(5588, body="Fixes an older report")
    result = run()
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    route = route_intake(evidence)
    assert route["route"] == "review_existing"
    assert {pr["number"] for pr in route["pull_requests"]} == {5588, 9001}
    group = Group(
        name="canonical",
        rationale="Verify the maintainer-selected existing contribution",
        branch="main",
        base_sha="b" * 40,
        items=[{"issue_number": 2026, "mode": "review"}],
    )
    selected = verification_specs(
        group,
        {
            "evidence": evidence,
            "route": route,
            "assessment": {"verification_prs": [5588]},
        },
        {},
    )
    assert len(selected) == 1 and selected[0].pr_number == 5588
    assert selected[0].head_sha == "c" * 40
    evidence["issue"]["labels"] = ["security"]
    assert route_intake(evidence)["route"] == "restricted_review"


def test_canonical_admission_does_not_follow_a_second_hop(evidence):
    from flows.agentic_intake import route_intake

    evidence["prs"] = [
        {**pr(1, "Fixes #2692"), "labels": []},
        {
            **pr(2, "Older issue"),
            "labels": [],
            "canonical_references": [{"source_pr": 1}],
        },
        {
            **pr(3, "Unrelated"),
            "labels": [],
            "canonical_references": [{"source_pr": 2}],
        },
    ]
    route = route_intake(evidence)
    assert {pr["number"] for pr in route["pull_requests"]} == {1, 2}
