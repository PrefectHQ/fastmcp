from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flows.agentic_contributors import maintainer_vouch
from flows.agentic_people import people
from flows.agentic_triage import vouched
from flows.agentic_vouch import (
    AFFILIATION,
    VOUCH,
    Graph,
    MemoryStore,
    VouchConfig,
    affiliation_changes,
    assess,
    decide,
    due_for_refresh,
    merged_authors,
    needs_assessment,
    observed_affiliations,
    sync_graph,
    vouch_config,
    vouch_record,
)

NOW = datetime(2026, 11, 23, tzinfo=UTC)
ALICE = {"id": 1, "login": "alice", "type": "User"}
BOB = {"id": 2, "login": "bob", "type": "User"}


def merged(
    number: int, user: dict, at: str = "2026-11-22T00:00:00Z", body: str = ""
) -> dict:
    return {
        "number": number,
        "user": user,
        "state": "closed",
        "created_at": at,
        "body": body,
        "pull_request": {"merged_at": at},
    }


def issue(number: int, user: dict, at: str) -> dict:
    return {"number": number, "user": user, "state": "open", "created_at": at}


def steady(user: dict, start: int = 10) -> list[dict]:
    """Three merges a month apart: the record of a real contributor."""
    return [
        merged(start, user, "2026-08-03T10:00:00Z"),
        merged(start + 1, user, "2026-09-07T10:00:00Z"),
        merged(start + 2, user, "2026-10-12T10:00:00Z"),
    ]


def store_with(*records: tuple[str, dict]) -> MemoryStore:
    store = MemoryStore()
    for collection, record in records:
        store.append(collection, record)
    return store


def test_config_is_off_unless_turned_on():
    assert vouch_config({}).mode == "off"
    assert vouch_config({"vouch": {"mode": "bogus"}}).mode == "off"
    assert vouch_config({"vouch": {"mode": "on"}}).mode == "on"


def test_unknown_person_has_no_standing():
    assert Graph([]).standing("nobody")["standing"] == "unknown"


CONFIG = VouchConfig(mode="on")


def lookup(items: list[dict]):
    numbers = {i["number"]: i for i in items}
    return numbers.get


def test_steady_work_over_months_passes():
    items = steady(ALICE)
    assert assess(items, lookup(items), CONFIG) == ([10, 11, 12], [])


def test_merge_count_alone_is_not_enough():
    items = [merged(n, ALICE, "2026-11-22T10:00:00Z") for n in range(10, 30)]
    assert assess(items, lookup(items), CONFIG)[1] == ["short", "burst"]


def test_staged_issue_then_pr_is_prebaked():
    items = []
    for k, month in enumerate(("08", "09", "10")):
        items.append(issue(100 + k, ALICE, f"2026-{month}-03T10:00:00Z"))
        items.append(
            merged(
                200 + k, ALICE, f"2026-{month}-03T10:02:00Z", body=f"Fixes #{100 + k}"
            )
        )
    assert assess(items, lookup(items), CONFIG)[1] == ["prebaked"]


def test_racing_other_peoples_new_issues_is_fast_follow():
    theirs = [
        issue(100 + k, BOB, f"2026-{m}-03T10:00:00Z")
        for k, m in enumerate(("08", "09", "10"))
    ]
    mine = [
        merged(200 + k, ALICE, f"2026-{m}-03T11:00:00Z", body=f"closes #{100 + k}")
        for k, m in enumerate(("08", "09", "10"))
    ]
    assert assess(mine, lookup(theirs + mine), CONFIG)[1] == ["fast-follow"]


def test_more_rejected_than_merged_is_held():
    closed = [
        {**merged(50 + n, ALICE), "pull_request": {"merged_at": None}} for n in range(4)
    ]
    items = steady(ALICE) + closed
    assert assess(items, lookup(items), CONFIG)[1] == ["rejected"]


def test_holds_name_reasons_and_are_not_vouches():
    record = decide(Graph([]), ALICE, [10], ["short"], False, NOW)
    assert (record["kind"], record["reason"]) == ("hold", "held: short")
    store = store_with((VOUCH, record))
    found = Graph(store.records()).standing("alice")
    assert found["standing"] == "unknown"
    assert found["held"] == "held: short"


def test_write_access_vouches_on_its_own():
    record = decide(Graph([]), ALICE, [10], ["short"], True, NOW)
    assert (record["kind"], record["reason"]) == ("vouch", "write access")


def test_nothing_new_writes_nothing():
    record = decide(Graph([]), ALICE, [10, 11, 12], [], False, NOW)
    graph = Graph(store_with((VOUCH, record)).records())
    assert decide(graph, ALICE, [10, 11, 12], [], False, NOW) is None
    assert needs_assessment(graph, merged_authors(steady(ALICE))) == []
    assert needs_assessment(graph, merged_authors([merged(99, ALICE)])) == [ALICE]


def test_bots_are_never_assessed():
    bot = {"id": 3, "login": "dependabot[bot]", "type": "Bot"}
    assert merged_authors([merged(5, bot)]) == {}


def test_a_vouch_that_goes_bad_becomes_a_hold():
    graph = Graph(
        store_with(
            (VOUCH, decide(Graph([]), ALICE, [10, 11, 12], [], False, NOW))
        ).records()
    )
    record = decide(graph, ALICE, [10, 11, 12, 13], ["burst"], False, NOW)
    assert record["kind"] == "hold"
    assert record["merged"] == [10, 11, 12, 13]


def test_maintainer_logins_are_one_edge_whatever_the_case():
    store = store_with(
        (VOUCH, vouch_record(ALICE, "denounce", "Nate", NOW)),
        (VOUCH, vouch_record(ALICE, "retract", "nate", NOW)),
    )
    assert Graph(store.records()).standing("alice")["standing"] == "unknown"


def test_an_old_login_does_not_resolve_after_someone_else_takes_it():
    renamed = {"id": 1, "login": "alice2"}
    squatter = {"id": 9, "login": "alice"}
    later = NOW + timedelta(days=1)
    store = store_with(
        (VOUCH, vouch_record(ALICE, "vouch", "nate", NOW)),
        (VOUCH, vouch_record(renamed, "vouch", "nate", later)),
        (AFFILIATION, affiliation_changes(Graph([]), squatter, set(), later)[0]),
    )
    graph = Graph(store.records())
    assert graph.standing("alice2")["standing"] == "vouched"
    assert graph.standing("alice")["standing"] == "unknown"


def test_a_maintainer_denounce_outweighs_a_triage_vouch_and_counts_as_a_reversal():
    store = store_with(
        (
            VOUCH,
            vouch_record(ALICE, "vouch", "triage", NOW, evidence=[10], merged=[10]),
        ),
        (VOUCH, vouch_record(ALICE, "vouch", "nate", NOW)),
        (VOUCH, vouch_record(ALICE, "denounce", "nate", NOW, reason="slop")),
    )
    found = Graph(store.records()).standing("Alice")
    assert found["standing"] == "denounced"
    assert found["denounced_by"] == ["nate"]
    assert found["vouched_by"] == ["triage"]
    assert found["reversals"] == 1


def test_a_retract_withdraws_only_that_edge():
    store = store_with(
        (VOUCH, vouch_record(ALICE, "denounce", "nate", NOW)),
        (VOUCH, vouch_record(ALICE, "retract", "nate", NOW)),
    )
    assert Graph(store.records()).standing("alice")["standing"] == "unknown"


def test_affiliation_comes_only_from_org_membership_and_at_mentions():
    user = {**ALICE, "company": "@PrefectHQ, ex-Acme Corp"}
    found = observed_affiliations(user, [{"login": "modelcontextprotocol"}])
    assert found == {
        ("prefecthq", "profile_company"),
        ("modelcontextprotocol", "github_org"),
    }


def test_standing_ranks_orgs_by_tier():
    store = store_with(
        (
            VOUCH,
            vouch_record(ALICE, "vouch", "triage", NOW, evidence=[1], merged=[1]),
        ),
    )
    graph = Graph(store.records())
    for record in affiliation_changes(
        graph,
        ALICE,
        {("acme", "github_org"), ("modelcontextprotocol", "github_org")},
        NOW,
    ):
        store.append(AFFILIATION, record)
    found = Graph(store.records()).standing(
        "alice", {"modelcontextprotocol": "mcp-spec"}
    )
    assert found["tier"] == "mcp-spec"
    assert [o["org"] for o in found["orgs"]] == ["modelcontextprotocol", "acme"]


def test_affiliations_that_go_away_are_marked_not_deleted():
    store = MemoryStore()
    for record in affiliation_changes(Graph([]), ALICE, {("acme", "github_org")}, NOW):
        store.append(AFFILIATION, record)
    later = NOW + timedelta(days=40)
    changes = affiliation_changes(Graph(store.records()), ALICE, set(), later)
    assert [(c["org"], c["present"]) for c in changes] == [("acme", False)]


def test_refresh_picks_vouched_people_never_looked_up_or_stale():
    store = store_with(
        (
            VOUCH,
            vouch_record(ALICE, "vouch", "triage", NOW, evidence=[1], merged=[1]),
        ),
        (
            VOUCH,
            vouch_record(BOB, "vouch", "triage", NOW, evidence=[2], merged=[1]),
        ),
        (AFFILIATION, affiliation_changes(Graph([]), BOB, set(), NOW)[0]),
    )
    graph = Graph(store.records())
    assert due_for_refresh(graph, NOW, 10) == [1]
    assert sorted(due_for_refresh(graph, NOW + timedelta(days=31), 10)) == [1, 2]


def test_backfill_assesses_from_the_listing_then_looks_up_affiliations():
    def read(path: str):
        return {
            "users/alice": {**ALICE, "company": "@prefecthq"},
            "users/alice/orgs": [],
        }[path]

    store = MemoryStore()
    items = steady(ALICE) + [merged(40, BOB)]
    result = sync_graph(store, items, read, CONFIG, NOW, complete=True)
    assert result == {
        "assessed": 2,
        "vouches": 1,
        "holds": 1,
        "affiliations_refreshed": 1,
    }
    graph = Graph(store.records())
    assert graph.standing("alice", {"PrefectHQ": "maintainer"})["tier"] == "maintainer"
    assert graph.standing("bob")["held"] == "held: short"
    again = sync_graph(store, items, read, CONFIG, NOW, complete=True)
    assert again["assessed"] == 0


def test_incremental_runs_look_authors_up_with_search():
    calls = []

    def read(path: str):
        calls.append(path)
        if path.startswith("search/issues") and "is%3Apr" in path:
            return {"items": steady(ALICE)}
        if path.startswith("search/issues"):
            return {"items": []}
        return {}

    store = MemoryStore()
    result = sync_graph(store, [steady(ALICE)[-1]], read, CONFIG, NOW)
    assert result["vouches"] == 1
    assert sum(c.startswith("search/issues") for c in calls) == 2


def maintainer_read(path: str):
    if path.endswith("/collaborators/nate/permission"):
        return {"permission": "admin", "role_name": "admin"}
    if "/collaborators/" in path:
        return {"permission": "read", "role_name": "read"}
    return {"users/alice": ALICE}.get(path)


def test_maintainers_vouch_by_their_own_login():
    store = MemoryStore()
    maintainer_vouch(
        store, maintainer_read, "alice", "denounce", "nate", "spam", [5], NOW
    )
    assert Graph(store.records()).standing("alice")["standing"] == "denounced"


@pytest.mark.parametrize("by", ["triage", "random"])
def test_only_maintainers_write_by_hand(by):
    with pytest.raises(ValueError):
        maintainer_vouch(
            MemoryStore(), maintainer_read, "alice", "vouch", by, "", [], NOW
        )


def test_people_cards_carry_graph_standing():
    store = store_with(
        (
            VOUCH,
            vouch_record(ALICE, "vouch", "triage", NOW, evidence=[1], merged=[1]),
        )
    )
    graph = Graph(store.records())

    def read(path: str):
        if "/collaborators/" in path:
            return {"permission": "read", "role_name": "read"}
        if path.startswith("users/"):
            return {
                "created_at": "2020-01-01T00:00:00Z",
                "followers": 1,
                "public_repos": 1,
            }
        if path.startswith("search/issues"):
            return {"total_count": 0, "items": []}
        return {"created_at": "2026-11-01T00:00:00Z"}

    evidence = {"issue": {"number": 7, "user": {"login": "alice"}}, "prs": []}
    cards = people(evidence, read, NOW, graph.standing)["cards"]
    assert cards[0]["graph"]["standing"] == "vouched"
    assert "graph" not in people(evidence, read, NOW)["cards"][0]


def test_vouched_senders_skip_the_burst_guard():
    store = store_with((VOUCH, vouch_record(ALICE, "vouch", "nate", NOW)))
    graph = Graph(store.records())
    assert vouched("alice", graph.standing)
    assert not vouched("bob", graph.standing)
