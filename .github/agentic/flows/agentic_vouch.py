"""Who vouches for whom on fastmcp, and who they work with: a private graph.

Modeled on mitchellh/vouch and tangled's `org.tangled.graph.vouch`. A vouch or
denounce is an edge with who made it, when, why and evidence (issue and PR
numbers). Nothing says what an edge means: standing is worked out when someone
asks, from whatever edges are current, so a change of rule never rewrites data.

Two record kinds, both append-only with TID keys, so every reversal stays:

- `io.prefect.fastmcp.contributors.vouch`: subject (GitHub id and login), kind
  (vouch, denounce, or retract to withdraw an earlier edge), by (a maintainer's
  login, or "triage"), reason, evidence, createdAt. The newest record per
  (by, subject) is that edge's current state. Same fields as tangled's record
  plus `by`, so the vouch half can be published later as a plain copy.
- `io.prefect.fastmcp.contributors.affiliation`: subject, org, source (a public
  GitHub org membership or the profile's company field), present, observedAt.
  Used only to weigh contributions. Never published.

Triage writes only what deterministic evidence supports: a vouch when a PR is
merged (only maintainers can merge), and affiliations from public GitHub data.
It never denounces or retracts; maintainers do that through the
`contributor-vouch` flow, which takes their login as `by`.

The records live in their own private space (a memberspace with every publish
flag off) owned by the authority account on the zds PDS in Prefect's GCP
project; flows sign in as that account with an app password. The space is
created on first use. `fastmcp_triage_config.vouch` holds the switch and the
account: `{"vouch": {"mode": "on", "pds": ..., "authority": ..., "handle": ...}}`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flows.agentic_work import PdsError, stamp, tid

SPACE_TYPE = "io.prefect.fastmcp.contributors"
VOUCH = "io.prefect.fastmcp.contributors.vouch"
AFFILIATION = "io.prefect.fastmcp.contributors.affiliation"
TRIAGE = "triage"
MAX_EVIDENCE = 10  # tangled's limit
MAX_REASON = 256
REFRESH_AFTER = timedelta(days=30)
PASSWORD_BLOCK = "zds-sec-authority-app-password"

Kind = Literal["vouch", "denounce", "retract"]
Tier = Literal["maintainer", "mcp-spec", "mcp-host", "downstream", "other"]
TIERS: tuple[str, ...] = ("maintainer", "mcp-spec", "mcp-host", "downstream", "other")

# GitHub org login -> tier. A starting guess nate can change in the config
# Variable without a deploy; anything unlisted is "other".
DEFAULT_ORGS: dict[str, Tier] = {
    "prefecthq": "maintainer",
    "modelcontextprotocol": "mcp-spec",
    "anthropics": "mcp-host",
    "openai": "mcp-host",
    "microsoft": "mcp-host",
    "google": "mcp-host",
    "github": "mcp-host",
    "cursor": "mcp-host",
    "pydantic": "downstream",
    "langchain-ai": "downstream",
    "agno-agi": "downstream",
    "arize-ai": "downstream",
    "mlflow": "downstream",
    "awslabs": "downstream",
    "snowflake-labs": "downstream",
    "neo4j-contrib": "downstream",
    "qdrant": "downstream",
}


class VouchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    mode: Literal["off", "on"] = "off"
    pds: str | None = None
    authority: str | None = None  # the space owner's DID
    handle: str | None = None
    orgs: dict[str, Tier] = Field(default_factory=lambda: dict(DEFAULT_ORGS))
    # Affiliation lookups per sync run, so a backfill can't eat the token's budget.
    refresh_per_run: int = Field(default=100, ge=0, le=1000)


def vouch_config(raw: object) -> VouchConfig:
    value = raw.get("vouch") if isinstance(raw, dict) else None
    try:
        return VouchConfig.model_validate(value or {})
    except ValidationError:
        return VouchConfig()


def subject(user: dict) -> dict:
    return {"id": int(user["id"]), "login": str(user["login"])}


def vouch_record(
    user: dict,
    kind: Kind,
    by: str,
    now: datetime,
    reason: str = "",
    evidence: Iterable[int] = (),
    evidence_count: int | None = None,
) -> dict:
    numbers = sorted(set(evidence))
    record = {
        "$type": VOUCH,
        "subject": subject(user),
        "kind": kind,
        "by": by,
        "evidence": numbers[-MAX_EVIDENCE:],
        "createdAt": stamp(now),
    }
    if reason:
        record["reason"] = reason[:MAX_REASON]
    if evidence_count is not None:
        record["evidenceCount"] = evidence_count
    return record


def affiliation_record(
    user: dict, org: str, source: str, present: bool, now: datetime
) -> dict:
    return {
        "$type": AFFILIATION,
        "subject": subject(user),
        "org": org,
        "source": source,
        "present": present,
        "observedAt": stamp(now),
    }


# ---------- reading: the graph and a person's standing ----------


@dataclass
class Person:
    id: int
    login: str
    edges: dict[str, dict] = field(default_factory=dict)  # by -> newest record
    affiliations: dict[tuple[str, str], dict] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)


class Graph:
    """Every record, folded into current edges per person. Pure: no I/O."""

    def __init__(self, records: Iterable[tuple[str, dict]]) -> None:
        self.people: dict[int, Person] = {}
        self._logins: dict[str, int] = {}
        # TID keys sort by time, so a stable sort on them replays the log.
        for _, record in sorted(records, key=lambda row: row[0]):
            who = record.get("subject") or {}
            if "id" not in who:
                continue
            person = self.people.setdefault(
                int(who["id"]), Person(int(who["id"]), who.get("login", ""))
            )
            person.login = who.get("login") or person.login
            self._logins[person.login.lower()] = person.id
            if record.get("$type") == VOUCH:
                person.edges[record["by"]] = record
                person.history.append(record)
            elif record.get("$type") == AFFILIATION:
                person.affiliations[(record["org"], record["source"])] = record

    def find(self, login: str) -> Person | None:
        key = self._logins.get(login.lower())
        return self.people.get(key) if key is not None else None

    def triage_edge(self, person_id: int) -> dict | None:
        person = self.people.get(person_id)
        return person.edges.get(TRIAGE) if person else None

    def last_observed(self, person_id: int) -> datetime | None:
        person = self.people.get(person_id)
        if not person or not person.affiliations:
            return None
        return max(_parse(r["observedAt"]) for r in person.affiliations.values())

    def standing(self, login: str, orgs: Mapping[str, str] | None = None) -> dict:
        """What the graph says about `login`, for an agent or a maintainer.

        denounced: a maintainer's current edge is a denounce; it outweighs any
        vouch. vouched: some current edge is a vouch. unknown: nothing current.
        Write access is not stored here; callers check it live.
        """
        person = self.find(login)
        if person is None:
            return {"login": login, "standing": "unknown", "vouched_by": [], "orgs": []}
        current = {
            by: edge for by, edge in person.edges.items() if edge["kind"] != "retract"
        }
        denounced = [by for by, e in current.items() if e["kind"] == "denounce"]
        vouched = [by for by, e in current.items() if e["kind"] == "vouch"]
        standing = "denounced" if denounced else "vouched" if vouched else "unknown"
        merged = current.get(TRIAGE, {})
        affiliations = sorted(
            {r["org"] for r in person.affiliations.values() if r.get("present")}
        )
        tiers = orgs or DEFAULT_ORGS
        ranked = sorted(
            (
                {"org": org, "tier": tiers.get(org.lower(), "other")}
                for org in affiliations
            ),
            key=lambda row: TIERS.index(row["tier"]),
        )
        return {
            "login": person.login,
            "standing": standing,
            "vouched_by": sorted(vouched),
            "denounced_by": sorted(denounced),
            "merged_prs": merged.get("evidenceCount", len(merged.get("evidence", [])))
            if merged.get("kind") == "vouch"
            else 0,
            "evidence": sorted(
                {n for e in current.values() for n in e.get("evidence", [])}
            )[-MAX_EVIDENCE:],
            "orgs": ranked,
            "tier": ranked[0]["tier"] if ranked else None,
            "reversals": _reversals(person.history),
        }


def _reversals(history: list[dict]) -> int:
    """How many times an edge changed kind (vouch to denounce, vouch to retract)."""
    last: dict[str, str] = {}
    count = 0
    for record in history:
        before = last.get(record["by"])
        if before is not None and before != record["kind"]:
            count += 1
        last[record["by"]] = record["kind"]
    return count


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------- writing: what triage adds from what it sees ----------


def merged_pr_vouches(graph: Graph, items: Iterable[dict], now: datetime) -> list[dict]:
    """A triage vouch per author with newly merged PRs among `items`.

    `items` is the GitHub issues listing the memory sync already pulls. Only
    maintainers can merge, so a merge is a maintainer's yes on that work. One
    record per author per run, carrying the newest evidence and a running count.
    """
    merged: dict[int, tuple[dict, set[int]]] = {}
    for item in items:
        user = item.get("user") or {}
        if not (item.get("pull_request") or {}).get("merged_at"):
            continue
        if user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]"):
            continue
        if "id" not in user:
            continue
        _, numbers = merged.setdefault(int(user["id"]), (user, set()))
        numbers.add(int(item["number"]))
    records = []
    for person_id, (user, numbers) in sorted(merged.items()):
        edge = graph.triage_edge(person_id) or {}
        known = set(edge.get("evidence", []))
        count = int(edge.get("evidenceCount", len(known)))
        new = numbers - known
        # The count can't tell old merges apart once evidence is capped, so
        # only numbers above the newest known one are counted as new.
        if known and len(known) >= MAX_EVIDENCE:
            new = {n for n in new if n > max(known)}
        if not new and edge.get("kind") == "vouch":
            continue
        total = count + len(new)
        records.append(
            vouch_record(
                user,
                "vouch",
                TRIAGE,
                now,
                reason=f"{total} merged PR{'s' if total != 1 else ''}",
                evidence=known | new,
                evidence_count=total,
            )
        )
    return records


COMPANY_ORG = re.compile(r"@([A-Za-z0-9-]{1,39})")


def observed_affiliations(user: dict, orgs: list[dict]) -> set[tuple[str, str]]:
    """(org, source) pairs public GitHub data shows for one user.

    Only two deterministic sources: public org memberships, and an `@org`
    mention in the profile's company field. Free-text company names are not
    guessed at.
    """
    found = {
        (str(org["login"]).lower(), "github_org") for org in orgs if org.get("login")
    }
    for match in COMPANY_ORG.findall(user.get("company") or ""):
        found.add((match.lower(), "profile_company"))
    return found


def affiliation_changes(
    graph: Graph, user: dict, seen: set[tuple[str, str]], now: datetime
) -> list[dict]:
    """Records for affiliations that appeared or went away since last time.

    A person with nothing to report gets one `present: false` marker so the
    refresh knows when it last looked.
    """
    person = graph.people.get(int(user["id"]))
    before = {
        key
        for key, r in (person.affiliations.items() if person else ())
        if r.get("present")
    }
    records = [
        affiliation_record(user, org, source, True, now)
        for org, source in sorted(seen - before)
    ] + [
        affiliation_record(user, org, source, False, now)
        for org, source in sorted(before - seen)
    ]
    if not records and (person is None or not person.affiliations):
        records.append(affiliation_record(user, "", "none", False, now))
    if not records:
        # Nothing changed: refresh the observation time on one record.
        org, source = sorted(before)[0] if before else ("", "none")
        records.append(affiliation_record(user, org, source, bool(before), now))
    return records


def due_for_refresh(graph: Graph, now: datetime, limit: int) -> list[int]:
    """People triage vouched for whose affiliations are missing or stale."""
    due = []
    for person_id, person in graph.people.items():
        if person.edges.get(TRIAGE, {}).get("kind") != "vouch":
            continue
        last = graph.last_observed(person_id)
        if last is None or now - last >= REFRESH_AFTER:
            due.append((last or datetime.min.replace(tzinfo=UTC), person_id))
    return [person_id for _, person_id in sorted(due)[:limit]]


# ---------- storage ----------


class Store(Protocol):
    def records(self) -> list[tuple[str, dict]]: ...

    def append(self, collection: str, record: dict) -> str: ...


class MemoryStore:
    """For tests and dry runs."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    def records(self) -> list[tuple[str, dict]]:
        return list(self.rows)

    def append(self, collection: str, record: dict) -> str:
        rkey = tid()
        self.rows.append((rkey, {**record, "$type": collection}))
        return rkey


class SpaceStore:
    """The private space on the zds PDS, written and read as its owner.

    Uses zds's permissioned-space XRPC methods (com.atproto.space.*). Records
    never reach the account's public repo.
    """

    def __init__(
        self,
        config: VouchConfig,
        password: Callable[[], str],
        client: httpx.Client | None = None,
    ) -> None:
        if not (config.pds and config.authority and config.handle):
            raise PdsError("vouch config needs pds, authority and handle")
        self.config = config
        self.base = config.pds.rstrip("/") + "/xrpc/"
        self.space = f"at://{config.authority}/space/{SPACE_TYPE}/self"
        self._password = password
        self._client = client or httpx.Client(timeout=20)
        self._token: str | None = None

    def _auth(self) -> dict:
        if self._token is None:
            response = self._client.post(
                self.base + "com.atproto.server.createSession",
                json={"identifier": self.config.handle, "password": self._password()},
            )
            _check(response, "createSession")
            self._token = response.json()["accessJwt"]
        return {"Authorization": f"Bearer {self._token}"}

    def ensure_space(self) -> None:
        """Create the space if it isn't there: private, members only."""
        response = self._client.get(
            self.base + "com.atproto.space.getSpace",
            params={"space": self.space},
            headers=self._auth(),
        )
        if response.status_code == 200:
            return
        response = self._client.post(
            self.base + "com.atproto.space.createSpace",
            json={
                "spaceType": SPACE_TYPE,
                "skey": "self",
                "policy": {"$type": "com.atproto.memberspace.policy"},
            },
            headers=self._auth(),
        )
        _check(response, "space.createSpace")

    def records(self) -> list[tuple[str, dict]]:
        rows: list[tuple[str, dict]] = []
        for collection in (VOUCH, AFFILIATION):
            cursor = None
            for _ in range(1000):
                params = {
                    "space": self.space,
                    "repo": self.config.authority,
                    "collection": collection,
                    "limit": 100,
                }
                if cursor:
                    params["cursor"] = cursor
                response = self._client.get(
                    self.base + "com.atproto.space.listRecords",
                    params=params,
                    headers=self._auth(),
                )
                _check(response, "space.listRecords")
                body = response.json()
                for row in body.get("records", []):
                    rkey = row.get("rkey") or row["uri"].rsplit("/", 1)[-1]
                    rows.append((rkey, {**row["value"], "$type": collection}))
                cursor = body.get("cursor")
                if not cursor or not body.get("records"):
                    break
        return rows

    def append(self, collection: str, record: dict) -> str:
        rkey = tid()
        response = self._client.post(
            self.base + "com.atproto.space.createRecord",
            json={
                "space": self.space,
                "repo": self.config.authority,
                "collection": collection,
                "rkey": rkey,
                "record": {**record, "$type": collection},
            },
            headers=self._auth(),
        )
        _check(response, "space.createRecord")
        return rkey


def _check(response: httpx.Response, method: str) -> None:
    if response.status_code >= 400:
        # never put the body in the message: it can carry tokens
        raise PdsError(f"{method} failed with HTTP {response.status_code}")


# ---------- one sync step, as the memory sync runs it ----------


def sync_graph(
    store: Store,
    items: list[dict],
    read: Callable[[str], object],
    config: VouchConfig,
    now: datetime,
) -> dict:
    """Add triage vouches for merges in `items`, then refresh some affiliations."""
    if isinstance(store, SpaceStore):
        store.ensure_space()
    graph = Graph(store.records())
    vouches = merged_pr_vouches(graph, items, now)
    for record in vouches:
        store.append(VOUCH, record)
    if vouches:
        graph = Graph(store.records())
    refreshed = 0
    for person_id in due_for_refresh(graph, now, config.refresh_per_run):
        login = graph.people[person_id].login
        try:
            user = read(f"users/{login}")
            orgs = read(f"users/{login}/orgs")
        except (RuntimeError, ValueError, KeyError, OSError, httpx.HTTPError):
            continue
        if not isinstance(user, dict) or not isinstance(orgs, list):
            continue
        for record in affiliation_changes(
            graph, user, observed_affiliations(user, orgs), now
        ):
            store.append(AFFILIATION, record)
        refreshed += 1
    return {"vouches": len(vouches), "affiliations_refreshed": refreshed}


# ---------- for triage runs: one lookup function per run ----------

Standing = Callable[[str], dict]


def load_standing() -> Standing | None:
    """A `login -> standing` lookup over the graph as it is now, or None when
    the graph is off or unreachable. Triage runs never fail on the graph."""
    from prefect.variables import Variable

    from flows.agentic_labels import CONFIG_VARIABLE
    from flows.agentic_stack_runtime import secret_sync

    try:
        config = vouch_config(Variable.get(CONFIG_VARIABLE, default={}))
        if config.mode == "off":
            return None
        graph = Graph(SpaceStore(config, lambda: secret_sync(PASSWORD_BLOCK)).records())
    except Exception:  # noqa: BLE001 - the graph is advisory; triage goes on without it
        logging.getLogger(__name__).warning(
            "Contributor graph unavailable", exc_info=True
        )
        return None
    orgs = dict(config.orgs)
    return lambda login: graph.standing(login, orgs)
