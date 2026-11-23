from datetime import UTC, datetime, timedelta

from flows.agentic_sweep import (
    SWEEP_LABEL,
    SweepConfig,
    candidates,
    close,
    due,
    propose,
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


def test_propose_lists_candidates_and_never_labels() -> None:
    # 11-23: the first live sweep labeled four confirmed bugs. A person labels now.
    github = FakeGitHub([item(1, 60)], {})
    assert [i["number"] for i in propose(github, SweepConfig(), NOW)] == [1]  # type: ignore[arg-type]


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
