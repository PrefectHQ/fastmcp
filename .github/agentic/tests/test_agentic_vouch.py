from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from flows.agentic_contributors import maintainer_vouch
from flows.agentic_people import people
from flows.agentic_triage import vouched
from flows.agentic_vouch import (
    AFFILIATION,
    MAX_EVIDENCE,
    VOUCH,
    Graph,
    MemoryStore,
    VouchConfig,
    affiliation_changes,
    due_for_refresh,
    merged_pr_vouches,
    observed_affiliations,
    sync_graph,
    vouch_config,
    vouch_record,
)

NOW = datetime(2026, 11, 23, tzinfo=UTC)
ALICE = {"id": 1, "login": "alice", "type": "User"}
BOB = {"id": 2, "login": "bob", "type": "User"}


def merged(number: int, user: dict) -> dict:
    return {
        "number": number,
        "user": user,
        "pull_request": {"merged_at": "2026-11-22T00:00:00Z"},
    }


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


def test_merges_become_one_triage_vouch_per_author():
    items = [
        merged(10, ALICE),
        merged(11, ALICE),
        merged(12, BOB),
        {"number": 13, "user": ALICE},
    ]
    records = merged_pr_vouches(Graph([]), items, NOW)
    by_login = {r["subject"]["login"]: r for r in records}
    assert by_login["alice"]["evidence"] == [10, 11]
    assert by_login["alice"]["merged"] == [10, 11]
    assert by_login["alice"]["by"] == "triage"
    assert by_login["bob"]["reason"] == "1 merged PR"


def test_bots_never_get_vouched():
    bot = {"id": 3, "login": "dependabot[bot]", "type": "Bot"}
    assert merged_pr_vouches(Graph([]), [merged(5, bot)], NOW) == []


def test_seen_merges_add_nothing_and_new_ones_extend_the_edge():
    store = store_with(
        (
            VOUCH,
            vouch_record(ALICE, "vouch", "triage", NOW, evidence=[10], merged=[10]),
        )
    )
    graph = Graph(store.records())
    assert merged_pr_vouches(graph, [merged(10, ALICE)], NOW) == []
    [record] = merged_pr_vouches(graph, [merged(10, ALICE), merged(20, ALICE)], NOW)
    assert record["evidence"] == [10, 20]
    assert record["reason"] == "2 merged PRs"


def test_out_of_order_merges_count_once_past_the_evidence_cap():
    counted = list(range(100, 140))
    store = store_with(
        (
            VOUCH,
            vouch_record(
                ALICE, "vouch", "triage", NOW, evidence=counted, merged=counted
            ),
        )
    )
    graph = Graph(store.records())
    # A backfill replays merges already counted.
    assert merged_pr_vouches(graph, [merged(100, ALICE)], NOW) == []
    # An old PR merged today counts, though its number is below the newest.
    [record] = merged_pr_vouches(graph, [merged(5, ALICE)], NOW)
    assert len(record["evidence"]) == MAX_EVIDENCE
    assert len(record["merged"]) == 41
    store.append(VOUCH, record)
    assert Graph(store.records()).standing("alice")["merged_prs"] == 41


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


def test_sync_vouches_merges_then_looks_up_affiliations():
    def read(path: str):
        return {
            "users/alice": {**ALICE, "company": "@prefecthq"},
            "users/alice/orgs": [],
        }[path]

    store = MemoryStore()
    result = sync_graph(store, [merged(10, ALICE)], read, VouchConfig(mode="on"), NOW)
    assert result == {"vouches": 1, "affiliations_refreshed": 1}
    found = Graph(store.records()).standing("alice", {"PrefectHQ": "maintainer"})
    assert found["tier"] == "maintainer"
    assert Graph(store.records()).standing("alice")["tier"] == "other"
    again = sync_graph(store, [merged(10, ALICE)], read, VouchConfig(mode="on"), NOW)
    assert again == {"vouches": 0, "affiliations_refreshed": 0}


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
