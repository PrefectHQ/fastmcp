from copy import deepcopy

import pytest

from flows.agentic_artifacts import plain, published_reviews


def test_posted_review_remains_visible_after_final_freshness_stop():
    receipt = {
        "pr_number": 5911,
        "review_id": 5691323410,
        "review_url": "https://github.com/PrefectHQ/fastmcp/pull/5911#pullrequestreview-5691323410",
        "head_sha": "a" * 40,
        "status": "posted",
    }
    result = {
        "batch_id": "recorded-review",
        "bins": {
            "operational": [
                {
                    "issue_number": 5910,
                    "status": "stale",
                    "reason": "Contribution changed after review",
                    "retained_outcomes": [
                        {
                            "bin": "existing_review",
                            "result": {"published_reviews": [receipt]},
                        }
                    ],
                }
            ],
            "existing_review": [
                {
                    "issue_number": 5910,
                    "published_reviews": [{**receipt, "status": "already_posted"}],
                }
            ],
        },
    }
    assert len(published_reviews(result)) == 1
    changed = deepcopy(result)
    changed["bins"]["existing_review"][0]["published_reviews"][0]["head_sha"] = "b" * 40
    with pytest.raises(ValueError, match="Conflicting retained"):
        published_reviews(changed)


def test_generated_draft_review_is_counted_with_existing_contribution_reviews():
    receipt = {
        "pr_number": 6141,
        "review_id": 5689075390,
        "review_url": "https://github.com/PrefectHQ/fastmcp/pull/6141#pullrequestreview-5689075390",
        "head_sha": "4e29f3dfd9597cc449af9552673c7d0e86120944",
        "status": "posted",
    }
    result = {
        "batch_id": "generated-draft",
        "bins": {
            "decisions": [{"review_delivery": {"posted": [receipt], "skipped": []}}],
            "existing_review": [{"published_reviews": [receipt]}],
        },
    }
    assert published_reviews(result) == [receipt]


def test_model_text_cannot_create_links_or_override_markdown_formatting():
    assert (
        plain("[open account settings](https://attacker.example)")
        == r"\[open account settings\]\(https://attacker\.example\)"
    )
    assert plain(None) == "unknown"
