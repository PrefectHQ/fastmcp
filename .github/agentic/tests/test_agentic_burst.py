from datetime import UTC, datetime
from urllib.parse import unquote

from flows.agentic_burst import BurstConfig, burst, burst_config, burst_line

NOW = datetime(2026, 11, 21, 21, 30, tzinfo=UTC)


def search(numbers: list[int], calls: list[str] | None = None):
    def read(endpoint: str) -> object:
        if calls is not None:
            calls.append(unquote(endpoint))
        return {"items": [{"number": n} for n in numbers]}

    return read


def test_under_the_limit_is_not_a_burst():
    assert burst(search([1, 2, 3]), "someone", 3, BurstConfig(max_items=3), NOW) is None


def test_only_the_first_item_past_the_limit_announces():
    numbers = list(range(6636, 6648))
    config = BurstConfig(max_items=5)
    found = [burst(search(numbers), "spammer", n, config, NOW) for n in numbers]
    assert found[:5] == [None] * 5
    assert [f["announce"] for f in found[5:]] == [True] + [False] * 6
    assert found[5]["count"] == 12


def test_search_is_scoped_to_the_repository_author_and_window():
    calls: list[str] = []
    burst(search([], calls), "someone", 1, BurstConfig(window_hours=24), NOW)
    assert calls == [
        "search/issues?q=repo:PrefectHQ/fastmcp author:someone created:>=2026-11-20T21:30:00Z&per_page=100"
    ]


def test_config_falls_back_to_defaults():
    assert burst_config({}) == BurstConfig()
    assert burst_config({"burst": {"max_items": "lots"}}) == BurstConfig()
    assert burst_config({"burst": {"max_items": 3}}).max_items == 3
    assert burst_config(None) == BurstConfig()


def test_line_names_the_author_and_count():
    line = burst_line({"login": "spammer", "count": 12, "window_hours": 24})
    assert "@spammer" in line and "12 issues and PRs" in line
