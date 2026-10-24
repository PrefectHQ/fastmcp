from copy import deepcopy

from flows.agentic_review_queue import plain, review_markdown


def test_queue_distinguishes_regression_operational_failure_and_stale_evidence():
    result = {
        "batch_id": "trial",
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "bins": {
            "existing_review": [
                {
                    "issue_number": 1,
                    "assessment": {"reason": "Existing contribution"},
                    "route": {"pull_requests": [{"number": 2, "held": True}]},
                    "verification": [
                        {
                            "pr_number": 2,
                            "head_sha": "a" * 40,
                            "baseline_sha": "b" * 40,
                            "run_id": "run",
                            "status": "verification_recorded",
                            "comparison": {
                                "fixed_tests": ["reported_bug"],
                                "regressed_tests": ["neighbor"],
                            },
                        }
                    ],
                }
            ]
        },
    }
    markdown = review_markdown(result)
    assert "A regression was reproduced" in markdown
    assert "Contribution holds retained" in markdown
    assert "1 fail-to-pass cases; 1 pass-to-fail cases" in markdown
    changed = deepcopy(result)
    item = changed["bins"]["existing_review"][0]
    item["status"] = "stale"
    item["verification"][0]["status"] = "environment_failure"
    markdown = review_markdown(changed)
    assert "Evidence changed" in markdown
    assert "Verification could not complete" in markdown
    assert "A regression was reproduced" not in markdown
    assert "Observed input tokens: 0" in markdown


def test_model_text_cannot_create_links_or_override_queue_formatting():
    assert (
        plain("[open account settings](https://attacker.example)")
        == r"\[open account settings\]\(https://attacker\.example\)"
    )
    assert plain(None) == "unknown"


def test_reproduction_queue_preserves_probe_and_contains_fence_like_output():
    probe = "diff --git a/test_probe.py b/test_probe.py\n+assert result == expected\n"
    observed = "Expected 200, received 404\n```\n[untrusted](https://example.com)"
    result = {
        "batch_id": "mre",
        "bins": {
            "evidence": [
                {
                    "issue_number": 603,
                    "assessment": {
                        "reason": "Investigate the reported routing failure"
                    },
                    "decision": "reproduced",
                    "base_sha": "a" * 40,
                    "contract": "The advertised route accepts messages",
                    "command": "uv run pytest test_probe.py",
                    "observed": observed,
                    "regression_patch": probe,
                }
            ]
        },
    }
    markdown = review_markdown(result)
    assert "**Reproduction outcome:** reproduced" in markdown
    assert "uv run pytest test_probe.py" in markdown
    assert f"````text\n{observed}\n````" in markdown
    assert f"```diff\n{probe}\n```" in markdown
    assert "a" * 40 in markdown
