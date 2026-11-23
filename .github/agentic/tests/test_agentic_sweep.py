from datetime import UTC, datetime, timedelta

from flows.agentic_sweep import (
    SWEEP_LABEL,
    SweepConfig,
    candidates,
    close,
    due,
    propose,
    vouched_for,
)

NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)


def stamp(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def item(number: int, idle: float, **extra) -> dict:
    return {
        "number": number,
        "title": f"item {number}",
        "state": "open",
        "user": {"type": "User", "login": "someone"},
        "author_association": "CONTRIBUTOR",
        "assignees": [],
        "labels": [],
        "updated_at": stamp(idle),
        **extra,
    }


def test_candidates_pick_inactive_outside_items_oldest_first() -> None:
    items = [
        item(1, 40),
        item(2, 90),
        item(3, 10),  # recent issue
        item(4, 25, pull_request={}),  # PR idle past 21 days
        item(5, 15, pull_request={}),  # recent PR
        item(6, 90, author_association="MEMBER"),
        item(7, 90, assignees=[{"login": "x"}]),
        item(8, 90, labels=[{"name": "security"}]),
        item(9, 90, labels=[{"name": SWEEP_LABEL}]),
        item(10, 90, user={"type": "Bot", "login": "renovate[bot]"}),
    ]
    picked = candidates(items, SweepConfig(), NOW)
    assert [i["number"] for i in picked] == [2, 1, 4]


def test_candidates_skip_private_maintainers_before_capping() -> None:
    items = [item(1, 90), item(2, 80), item(3, 70)]
    items[0]["user"]["login"] = "hidden-maintainer"
    picked = candidates(
        items, SweepConfig(max_items=2), NOW, lambda login: login == "hidden-maintainer"
    )
    assert [i["number"] for i in picked] == [2, 3]


def test_candidates_respect_cap() -> None:
    items = [item(n, 60 + n) for n in range(10)]
    picked = candidates(items, SweepConfig(max_items=3), NOW)
    assert [i["number"] for i in picked] == [9, 8, 7]


def labeled(hours: float) -> dict:
    return {
        "event": "labeled",
        "label": {"name": SWEEP_LABEL},
        "created_at": (NOW - timedelta(hours=hours)).isoformat(),
    }


def comment(hours: float, kind: str = "User") -> dict:
    return {
        "event": "commented",
        "user": {"type": kind},
        "created_at": (NOW - timedelta(hours=hours)).isoformat(),
    }


def test_due_after_veto_window_without_human_comment() -> None:
    config = SweepConfig()
    assert due({}, [labeled(49)], config, NOW)
    assert due({}, [labeled(49), comment(10, "Bot")], config, NOW)
    assert due({}, [comment(60), labeled(49)], config, NOW)
    assert not due({}, [labeled(47)], config, NOW)
    assert not due({}, [labeled(49), comment(10)], config, NOW)
    assert not due({}, [], config, NOW)
    # Relabeled recently: the window restarts.
    assert not due({}, [labeled(100), labeled(5)], config, NOW)


class FakeGitHub:
    def __init__(self, items: list[dict], timelines: dict[int, list[dict]]):
        self.items = items
        self.timelines = timelines

    def request(self, path: str, body=None, *, method=None):
        return {"role_name": "read", "permission": "read"}

    def pages(self, path: str) -> list[dict]:
        if "/timeline" in path:
            return self.timelines[int(path.split("/")[-2])]
        if "labels=" in path:
            return [
                i
                for i in self.items
                if SWEEP_LABEL in {label["name"] for label in i["labels"]}
            ]
        return self.items


def test_propose_dry_run_writes_nothing_and_on_labels() -> None:
    github = FakeGitHub([item(1, 60)], {1: []})
    assert [i["number"] for i in propose(github, None, SweepConfig(), NOW)] == [1]  # type: ignore[arg-type]
    calls = []

    def write(path, body=None, method=None):
        calls.append((path, body))
        return {"name": SWEEP_LABEL} if path.endswith("labels/sweep%3Aclose") else {}

    propose(github, write, SweepConfig(), NOW)  # type: ignore[arg-type]
    assert calls[-1] == (
        "repos/PrefectHQ/fastmcp/issues/1/labels",
        {"labels": [SWEEP_LABEL]},
    )


def test_close_comments_then_closes_due_items_only() -> None:
    marked = [{"name": SWEEP_LABEL}]
    github = FakeGitHub(
        [item(1, 3, labels=marked), item(2, 3, labels=marked, pull_request={})],
        {1: [labeled(50)], 2: [labeled(50), comment(1)]},
    )
    calls = []
    closed = close(  # type: ignore[arg-type]
        github,
        lambda path, body=None, method=None: calls.append((path, body, method)),
        SweepConfig(),
        NOW,
    )
    assert [i["number"] for i in closed] == [1]
    assert calls[0][0] == "repos/PrefectHQ/fastmcp/issues/1/comments"
    assert calls[1] == (
        "repos/PrefectHQ/fastmcp/issues/1",
        {"state": "closed", "state_reason": "not_planned"},
        "PATCH",
    )


def maintainer_comment(login: str = "jlowin", association: str = "MEMBER") -> dict:
    return {
        "event": "commented",
        "user": {"type": "User", "login": login},
        "author_association": association,
    }


def open_pr(number: int, repository: str = "PrefectHQ/fastmcp") -> dict:
    return {
        "event": "cross-referenced",
        "source": {
            "issue": {
                "number": number,
                "state": "open",
                "pull_request": {},
                "repository": {"full_name": repository},
            }
        },
    }


def test_sweep_keeps_items_a_maintainer_vouched_for() -> None:
    # #2154 and #3680: a maintainer endorsed the request, then nobody built it.
    assert vouched_for(item(1, 90), [maintainer_comment()], lambda login: False)
    # A maintainer whose org membership is private shows up as a contributor.
    assert vouched_for(
        item(1, 90),
        [maintainer_comment("hidden", "CONTRIBUTOR")],
        lambda login: login == "hidden",
    )
    # #4927 and #5182: an open fix PR here refers to the issue.
    assert vouched_for(item(1, 90), [open_pr(5235)], lambda login: False)


def test_sweep_still_takes_items_nobody_vouched_for() -> None:
    def never(login: str) -> bool:
        return False

    assert not vouched_for(item(1, 90), [], never)
    assert not vouched_for(item(1, 90), [comment(5)], never)
    assert not vouched_for(item(1, 90), [open_pr(9, "someone/fork")], never)
    closed = open_pr(9)
    closed["source"]["issue"]["state"] = "closed"
    assert not vouched_for(item(1, 90), [closed], never)
    # A maintainer asked the reporter for a reproducer and never got one.
    asked = item(1, 90, labels=[{"name": "needs MRE"}])
    assert not vouched_for(asked, [maintainer_comment()], never)


def test_propose_skips_vouched_items_before_capping() -> None:
    github = FakeGitHub(
        [item(1, 90), item(2, 80), item(3, 70)],
        {1: [open_pr(5)], 2: [], 3: []},
    )
    picked = propose(github, None, SweepConfig(max_items=2), NOW)  # type: ignore[arg-type]
    assert [i["number"] for i in picked] == [2, 3]
