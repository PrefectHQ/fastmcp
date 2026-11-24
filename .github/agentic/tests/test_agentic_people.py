from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from flows.agentic_people import people

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def reader(
    users: dict,
    counts: dict,
    missing: set[str] = frozenset(),
    maintainers: set[str] = frozenset(),
):
    def read(endpoint: str) -> dict:
        if "/collaborators/" in endpoint:
            login = endpoint.split("/collaborators/")[1].split("/")[0]
            if login in missing:
                raise RuntimeError("gh api exited 1: rate limited")
            role = "admin" if login in maintainers else "read"
            return {"permission": role, "role_name": role}
        if endpoint.startswith("users/"):
            login = endpoint.removeprefix("users/")
            if login in missing:
                raise RuntimeError("gh api exited 1: rate limited")
            return users[login]
        if endpoint.startswith("search/issues"):
            query = parse_qs(urlparse(endpoint).query)["q"][0]
            for key, value in counts.items():
                if key in query:
                    return value
            return {"total_count": 0, "items": []}
        if endpoint.endswith("/issues/7"):
            return {"created_at": "2026-11-01T10:00:00Z"}
        if endpoint.endswith("/pulls/8"):
            return {"created_at": "2026-11-01T11:30:00Z"}
        raise AssertionError(endpoint)

    return read


def evidence(*prs):
    return {"issue": {"number": 7, "user": {"login": "reporter"}}, "prs": list(prs)}


def pr(number, login, kind="User"):
    return {"number": number, "author": {"login": login, "type": kind}}


def test_cards_give_the_agent_what_a_maintainer_would_glance_at():
    users = {
        "reporter": {
            "created_at": "2016-03-01T00:00:00Z",
            "followers": 3000,
            "public_repos": 80,
        },
        "sweeper": {
            "created_at": "2025-09-13T00:00:00Z",
            "followers": 4,
            "public_repos": 20,
        },
    }
    elsewhere = {
        "total_count": 214,
        "items": [
            {"repository_url": f"https://api.github.com/repos/o/r{i % 40}"}
            for i in range(100)
        ],
    }
    counts = {
        "author:sweeper -repo": elsewhere,
        "author:sweeper is:merged": {"total_count": 4},
        "author:sweeper is:closed is:unmerged": {"total_count": 7},
        "author:reporter is:merged": {"total_count": 12},
    }
    result = people(
        evidence(pr(8, "reporter"), pr(9, "sweeper"), pr(10, "renovate[bot]", "Bot")),
        reader(users, counts),
        NOW,
    )
    cards = {card["login"]: card for card in result["cards"]}
    assert set(cards) == {"reporter", "sweeper"}
    assert cards["reporter"]["followers"] == 3000
    assert cards["reporter"]["prs_merged_here"] == 12
    assert cards["reporter"]["role"] == "reporter"
    assert cards["reporter"]["maintainer"] is False
    assert cards["sweeper"] == {
        "login": "sweeper",
        "role": "pr_author",
        "maintainer": False,
        "account_created": "2025-09-13",
        "followers": 4,
        "public_repos": 20,
        "prs_merged_here": 4,
        "prs_closed_unmerged_here": 7,
        "prs_elsewhere_last_6_months": 214,
        "repos_in_newest_100_of_those": 40,
    }
    assert result["reporter_prs_opened_hours_after_issue"] == {"8": 1.5}


def test_a_failed_lookup_leaves_a_gap_instead_of_failing_the_assessment():
    result = people(
        evidence(pr(9, "someone")), reader({}, {}, {"reporter", "someone"}), NOW
    )
    assert result["cards"] == [
        {
            "login": "reporter",
            "unavailable": "RuntimeError",
            "role": "reporter",
            "maintainer": False,
        },
        {
            "login": "someone",
            "unavailable": "RuntimeError",
            "role": "pr_author",
            "maintainer": False,
        },
    ]


def test_a_timed_out_lookup_leaves_a_gap_too():
    def read(endpoint: str) -> dict:
        raise subprocess.TimeoutExpired(["gh", "api", endpoint], 30)

    result = people(evidence(pr(9, "someone")), read, NOW)
    assert result["issue_opened_at"] is None
    assert [card["unavailable"] for card in result["cards"]] == [
        "TimeoutExpired",
        "TimeoutExpired",
    ]


def test_the_elsewhere_sample_is_the_newest_prs():
    seen = []

    def read(endpoint: str) -> dict:
        seen.append(endpoint)
        if endpoint.startswith("users/"):
            return {
                "created_at": "2020-01-01T00:00:00Z",
                "followers": 1,
                "public_repos": 1,
            }
        return {"total_count": 0, "items": [], "created_at": "2026-11-01T00:00:00Z"}

    people(evidence(), read, NOW)
    elsewhere = next(
        e for e in seen if "-repo" in parse_qs(urlparse(e).query).get("q", [""])[0]
    )
    query = parse_qs(urlparse(elsewhere).query)
    assert query["sort"] == ["created"] and query["order"] == ["desc"]


def test_maintainers_come_from_write_access_not_history():
    # #6692: a maintainer's own report was treated as an outsider's, and a
    # commenter asking for assignment was credited as the reporter.
    issue = {
        "issue": {"number": 7, "user": {"login": "reporter"}},
        "prs": [],
        "comments": [
            {"author": {"login": "helper", "type": "User"}},
            {"author": {"login": "bot", "type": "Bot"}},
        ],
    }
    users = {
        login: {"created_at": "2020-01-01", "followers": 1, "public_repos": 1}
        for login in ("reporter", "helper")
    }
    result = people(issue, reader(users, {}, maintainers={"reporter"}), NOW)
    assert result["maintainers"] == ["reporter"]
    assert result["maintainer_check_failed"] == []
    (card,) = result["cards"]
    assert (card["login"], card["role"], card["maintainer"]) == (
        "reporter",
        "reporter",
        True,
    )
