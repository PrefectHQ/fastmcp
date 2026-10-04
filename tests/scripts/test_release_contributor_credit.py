import json
from typing import Any

import pytest

from scripts.release_contributor_credit import fetch_prs, scan_notes, summarize_credit


def actor(login: str, kind: str = "User") -> dict[str, str]:
    return {"login": login, "__typename": kind}


def pr_metadata(
    number: int,
    author: str = "maintainer",
    coauthors: tuple[str, ...] = (),
    reporters: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "number": number,
        "author": actor(author),
        "closingIssuesReferences": {
            "nodes": [
                {
                    "number": i,
                    "url": f"https://github.com/PrefectHQ/fastmcp/issues/{i}",
                    "author": actor(login),
                }
                for i, login in enumerate(reporters, 1)
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "mergeCommit": {
            "authors": {
                "nodes": [
                    {
                        "name": login,
                        "email": f"{login}@users.noreply.github.com",
                        "user": actor(login),
                    }
                    for login in (author, *coauthors)
                ],
                "pageInfo": {"hasNextPage": False},
            }
        },
    }


def entry(number: int, author: str = "maintainer") -> str:
    return f"* Fix lookup by @{author} in https://github.com/PrefectHQ/fastmcp/pull/{number}\n"


def mock_github(
    monkeypatch: pytest.MonkeyPatch,
    prs: list[dict[str, Any]],
    permission: str = "write",
    maintainers: tuple[str, ...] = ("maintainer",),
) -> list[list[str]]:
    calls: list[list[str]] = []

    def respond(command: list[str], **kwargs: Any) -> str:
        calls.append(command)
        if command[2] == "graphql":
            return json.dumps(
                {"data": {"repository": {f"p{pr['number']}": pr for pr in prs}}}
            )
        assert command[2].endswith("/permission")
        login = command[2].split("/")[-2]
        return json.dumps(
            {"permission": permission if login in maintainers else "read"}
        )

    monkeypatch.setattr(
        "scripts.release_contributor_credit.subprocess.check_output", respond
    )
    return calls


def test_batch_credits_missing_reporters_and_reuses_maintainer_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = mock_github(
        monkeypatch,
        [
            pr_metadata(1, reporters=("reporter",)),
            pr_metadata(2, reporters=("reporter",)),
        ],
    )
    result = scan_notes(entry(1) + entry(2))
    assert [candidate["credit"] for candidate in result] == [
        ["maintainer", "reporter"],
        ["maintainer", "reporter"],
    ]
    assert result[0]["missing_reporter_coauthors"] == ["reporter"]
    assert "by @maintainer and @reporter in" in result[0]["suggested_entry"]
    assert len(calls) == 3  # Metadata and one permission lookup per distinct account.


def test_human_coauthors_survive_agent_filter_without_an_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr = pr_metadata(1, coauthors=("helper", "claude", "codex", "automation[bot]"))
    pr["mergeCommit"]["authors"]["nodes"].append(
        {"name": "Claude Opus 5.5", "email": "noreply@anthropic.com", "user": None}
    )
    calls = mock_github(monkeypatch, [pr])
    result = scan_notes(entry(1))
    assert result[0]["credit"] == ["maintainer", "helper"]
    assert result[0]["unmapped_commit_authors"] == []
    assert len(calls) == 2


def test_external_pr_does_not_automatically_credit_distinct_issue_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(
        monkeypatch,
        [pr_metadata(1, author="contributor", reporters=("reporter",))],
        permission="read",
    )
    assert scan_notes(entry(1, "contributor")) == []


def test_deduplicates_reporters_and_renders_oxford_comma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(
        monkeypatch,
        [
            pr_metadata(
                1, coauthors=("helper",), reporters=("helper", "reporter", "reporter")
            )
        ],
    )
    result = scan_notes(entry(1))
    assert result[0]["credit"] == ["maintainer", "helper", "reporter"]
    assert "by @maintainer, @helper, and @reporter in" in result[0]["suggested_entry"]


def test_unmapped_human_author_is_flagged_without_guessing_a_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr = pr_metadata(1)
    pr["mergeCommit"]["authors"]["nodes"].append(
        {"name": "Helpful Person", "email": "person@example.com", "user": None}
    )
    mock_github(monkeypatch, [pr])
    result = scan_notes(entry(1))
    assert result[0]["unmapped_commit_authors"] == ["Helpful Person"]
    assert result[0]["credit"] == ["maintainer"]


def test_changed_notes_format_fails_visibly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = mock_github(monkeypatch, [])
    with pytest.raises(ValueError, match="Cannot parse release entries"):
        scan_notes("* Fix lookup (https://github.com/PrefectHQ/fastmcp/pull/1)")
    assert calls == []


@pytest.mark.parametrize("connection", ["issues", "authors"])
def test_truncated_metadata_fails_visibly(
    monkeypatch: pytest.MonkeyPatch, connection: str
) -> None:
    pr = pr_metadata(1)
    nodes = (
        pr["closingIssuesReferences"]
        if connection == "issues"
        else pr["mergeCommit"]["authors"]
    )
    nodes["pageInfo"]["hasNextPage"] = True
    mock_github(monkeypatch, [pr])
    with pytest.raises(ValueError, match="truncated"):
        fetch_prs([1])


def test_bot_authored_generated_entry_is_parsed_without_crediting_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(monkeypatch, [pr_metadata(1, author="dependabot[bot]")])
    assert scan_notes(entry(1, "dependabot[bot]")) == []


def test_single_human_coauthor_of_bot_pr_is_credited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(
        monkeypatch, [pr_metadata(1, author="automation[bot]", coauthors=("helper",))]
    )
    result = scan_notes(entry(1, "automation[bot]"))
    assert result[0]["credit"] == ["helper"]
    assert "by @helper in" in result[0]["suggested_entry"]


def test_summary_counts_unique_humans_and_flags_first_time_candidates() -> None:
    notes = (
        entry(1)
        + entry(2)
        + entry(3, "dependabot[bot]")
        + "\n## New Contributors\n"
        + "* @maintainer made their first contribution in https://github.com/PrefectHQ/fastmcp/pull/1\n"
        + "* @claude made their first contribution in https://github.com/PrefectHQ/fastmcp/pull/2\n"
    )
    candidates = [
        {"credit": ["maintainer", "reporter"]},
        {"credit": ["Maintainer", "reporter", "helper"]},
    ]
    result = summarize_credit(notes, candidates)
    assert result["contributors"] == ["helper", "maintainer", "reporter"]
    assert result["total_contributors"] == 3
    assert result["generated_first_time_contributors"] == ["maintainer"]
    assert result["generated_first_time_count"] == 1
    assert result["first_time_review"] == ["helper", "maintainer", "reporter"]


def test_maintainer_report_and_coauthor_need_no_supplemental_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(
        monkeypatch,
        [
            pr_metadata(
                1, coauthors=("othermaintainer",), reporters=("othermaintainer",)
            )
        ],
        maintainers=("maintainer", "othermaintainer"),
    )
    assert scan_notes(entry(1)) == []


def test_existing_maintainer_credit_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = mock_github(
        monkeypatch,
        [pr_metadata(1, coauthors=("othermaintainer",))],
        maintainers=("maintainer", "othermaintainer"),
    )
    notes = entry(1).replace(
        "by @maintainer in", "by @maintainer and @othermaintainer in"
    )
    assert scan_notes(notes) == []
    assert len(calls) == 1
