from __future__ import annotations

import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

import pytest

from flows.agentic_stack_context import assessment_context, checks, comparison

BASE, REVIEW, HEAD = (letter * 40 for letter in "abc")


@pytest.fixture
def source():
    responses = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            value = responses.get(self.path)
            self.send_response(200 if value is not None else 404)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read(endpoint):
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/{endpoint}", timeout=2
        ) as response:
            return json.load(response)

    try:
        yield responses, read
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def change(patch):
    return {
        "status": "ahead",
        "ahead_by": 1,
        "total_commits": 1,
        "commits": [{"sha": HEAD, "commit": {"message": "Fix reviewed binding"}}],
        "files": [{"filename": "source.py", "status": "modified", "patch": patch}],
    }


@pytest.mark.parametrize("state", ["open", "closed"])
def test_original_review_revision_and_human_history_survive_projection(source, state):
    responses, read = source
    responses[f"/repos/PrefectHQ/fastmcp/compare/{BASE}...{HEAD}?per_page=100"] = (
        change("+current implementation")
    )
    responses[f"/repos/PrefectHQ/fastmcp/compare/{REVIEW}...{HEAD}?per_page=100"] = (
        change("-ForwardRef('Root')\n+ForwardRef(enclosing_class)")
    )
    responses["/repos/PrefectHQ/fastmcp/pulls/10"] = {
        "head": {"sha": HEAD},
        "updated_at": "now",
    }
    raw = {
        "complete": True,
        "issue": {"number": 1},
        "comments": [
            {
                "id": 1,
                "body": "Superseded by #10, not a rejection.",
                "user": {"login": "maintainer"},
            }
        ],
        "timeline": [
            {
                "event": "reopened",
                "actor": {"login": "maintainer"},
                "source": {"issue": {"number": 10, "body": "redundant" * 10000}},
            }
        ],
        "prs": [
            {
                "number": 10,
                "state": state,
                "merged": False,
                "base_sha": BASE,
                "head_sha": HEAD,
                "updated_at": "now",
                "reviews": [],
                "review_comments": [
                    {
                        "body": "Root binding is broken",
                        "commit_id": HEAD,
                        "original_commit_id": REVIEW,
                    }
                ],
            }
        ],
    }
    original = deepcopy(raw)
    result = assessment_context(
        {"evidence": raw, "route": {"held": True}, "group_rationale": "schemas"}, read
    )
    pr = result["evidence"]["prs"][0]
    delta = pr["revision_evidence"]["changes_since_review"][0]
    assert delta["base_sha"] == REVIEW
    assert "+ForwardRef(enclosing_class)" in delta["files"][0]["patch"]
    assert delta["complete"] and not delta["tests_executed"]
    assert result["route"]["held"]
    assert result["evidence"]["comments"][0]["body"] == raw["comments"][0]["body"]
    assert result["evidence"]["timeline"][0]["event"] == "reopened"
    assert len(json.dumps(result)) < len(json.dumps(raw)) / 10
    assert raw == original
    responses["/repos/PrefectHQ/fastmcp/pulls/10"]["head"]["sha"] = BASE
    with pytest.raises(ValueError, match="changed"):
        assessment_context(
            {"evidence": raw, "route": {}, "group_rationale": "schemas"}, read
        )


def test_truncated_comparison_never_claims_complete_review(source):
    responses, read = source
    endpoint = f"/repos/PrefectHQ/fastmcp/compare/{REVIEW}...{HEAD}?per_page=100"
    responses[endpoint] = change("+large patch")
    result = comparison(read, REVIEW, HEAD, 1)
    assert not result["complete"]
    assert result["omitted_patches"] == ["source.py"]
    assert result["files"][0]["patch_unavailable"] == "budget"
    responses[endpoint]["files"][0].pop("patch")
    assert not comparison(read, REVIEW, HEAD, 100)["complete"]
    responses[endpoint] = {**change("+fixed"), "total_commits": 101}
    assert not comparison(read, REVIEW, HEAD, 100)["complete"]


def test_missing_checks_and_failed_gates_cannot_become_green():
    assert checks({"head_sha": HEAD})["absent"]
    result = checks(
        {
            "head_sha": HEAD,
            "checks": [
                {"name": "tests", "conclusion": "success"},
                {"name": "issue-gate", "conclusion": "failure"},
            ],
        }
    )
    assert not result["absent"]
    assert result["check_counts"] == {"success": 1, "failure": 1}
    assert result["checks"] == [{"name": "issue-gate", "conclusion": "failure"}]


def test_comparison_metadata_cannot_overrun_the_model_context(source):
    responses, read = source
    endpoint = f"/repos/PrefectHQ/fastmcp/compare/{REVIEW}...{HEAD}?per_page=100"
    responses[endpoint] = {
        **change("+fixed"),
        "total_commits": 100,
        "commits": [
            {"sha": f"{n:040x}", "commit": {"message": "Long history\n" * 500}}
            for n in range(100)
        ],
    }
    result = comparison(read, REVIEW, HEAD, 12000)
    assert len(json.dumps(result).encode()) <= 16096
    assert result["complete"] is False
    assert result["base_sha"] == REVIEW and result["head_sha"] == HEAD
    assert result["comparison_url"].endswith(f"{REVIEW}...{HEAD}")
    assert result["details_omitted"] == "comparison_exceeds_context_budget"
    assert result["total_commits"] == 100
    assert result["returned_file_count"] == 1
    assert not result["tests_executed"]
