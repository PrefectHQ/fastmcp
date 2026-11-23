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
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flows.agentic_work import PdsError, stamp, tid

SPACE_TYPE = "io.prefect.fastmcp.contributors"
VOUCH = "io.prefect.fastmcp.contributors.vouch"
AFFILIATION = "io.prefect.fastmcp.contributors.affiliation"
TRIAGE = "triage"
REPOSITORY = "PrefectHQ/fastmcp"
MAX_EVIDENCE = 10  # tangled's limit
MAX_REASON = 256
REFRESH_AFTER = timedelta(days=30)
PASSWORD_BLOCK = "zds-sec-authority-app-password"

Kind = Literal["vouch", "denounce", "retract", "hold"]
Tier = Literal["maintainer", "mcp-spec", "mcp-host", "downstream", "other"]
TIERS: tuple[str, ...] = ("maintainer", "mcp-spec", "mcp-host", "downstream", "other")

# GitHub org login -> tier lives only in the config Variable, never in code:
# `{"vouch": {"orgs": {"some-org": "mcp-host"}}}`. Unlisted orgs are "other".


class VouchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    mode: Literal["off", "on"] = "off"
    pds: str | None = None
    authority: str | None = None  # the space owner's DID
    handle: str | None = None
    orgs: dict[str, Tier] = Field(default_factory=dict)
    # Affiliation lookups per sync run, so a backfill can't eat the token's budget.
    refresh_per_run: int = Field(default=100, ge=0, le=1000)
    # What a contributor's record must show before triage vouches. Write
    # access is enough on its own; everyone else needs all of these.
    min_merge_weeks: int = Field(default=3, ge=1)  # merges in this many ISO weeks
    min_span_days: int = Field(default=30, ge=0)  # first to last merge
    burst_per_day: int = Field(default=15, ge=1)  # issues + PRs opened in one day
    prebaked_minutes: int = Field(default=10, ge=0)  # own issue, then PR this fast
    fast_follow_hours: int = Field(default=6, ge=0)  # PR on someone else's new issue
    assess_per_run: int = Field(default=40, ge=0, le=500)


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
    merged: Iterable[int] | None = None,
) -> dict:
    numbers = sorted(set(evidence))
    record = {
        "$type": VOUCH,
        "subject": subject(user),
        "kind": kind,
        "by": by.lower(),
        "evidence": numbers[-MAX_EVIDENCE:],
        "createdAt": stamp(now),
    }
    if reason:
        record["reason"] = reason[:MAX_REASON]
    if merged is not None:
        # Every merged PR counted so far, so a late or out-of-order merge is
        # still told apart from one already counted. Private; dropped on export.
        record["merged"] = sorted(set(merged))
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
            if record.get("$type") == VOUCH:
                person.edges[str(record["by"]).lower()] = record
                person.history.append(record)
            elif record.get("$type") == AFFILIATION:
                person.affiliations[(record["org"], record["source"])] = record

        # Only each person's newest login resolves, and the newest holder of
        # a login wins, so a rename never lends one account's edges to another.
        newest: dict[str, tuple[str, int]] = {}
        for person in self.people.values():
            seen = max(
                [r["createdAt"] for r in person.history]
                + [r["observedAt"] for r in person.affiliations.values()],
                default="",
            )
            key = person.login.lower()
            if key not in newest or seen > newest[key][0]:
                newest[key] = (seen, person.id)
        self._logins = {key: person_id for key, (_, person_id) in newest.items()}

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
            by: edge
            for by, edge in person.edges.items()
            if edge["kind"] not in ("retract", "hold")
        }
        denounced = [by for by, e in current.items() if e["kind"] == "denounce"]
        vouched = [by for by, e in current.items() if e["kind"] == "vouch"]
        standing = "denounced" if denounced else "vouched" if vouched else "unknown"
        merged = current.get(TRIAGE, {})
        affiliations = sorted(
            {r["org"] for r in person.affiliations.values() if r.get("present")}
        )
        tiers = {org.lower(): tier for org, tier in (orgs or {}).items()}
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
            "merged_prs": len(merged.get("merged", merged.get("evidence", [])))
            if merged.get("kind") == "vouch"
            else 0,
            "evidence": sorted(
                {n for e in current.values() for n in e.get("evidence", [])}
            )[-MAX_EVIDENCE:],
            "orgs": ranked,
            "tier": ranked[0]["tier"] if ranked else None,
            "reversals": _reversals(person.history),
            "held": person.edges.get(TRIAGE, {}).get("reason")
            if person.edges.get(TRIAGE, {}).get("kind") == "hold"
            else None,
        }


def _reversals(history: list[dict]) -> int:
    """How many times an edge changed kind (vouch to denounce, vouch to retract)."""
    last: dict[str, str] = {}
    count = 0
    for record in history:
        by = str(record["by"]).lower()
        if last.get(by) is not None and last[by] != record["kind"]:
            count += 1
        last[by] = record["kind"]
    return count


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------- writing: what triage adds from what it sees ----------


FIXES = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
    r"(?:https://github\.com/PrefectHQ/fastmcp/issues/|#)(\d+)",
    re.IGNORECASE,
)


def _is_bot(user: dict) -> bool:
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def merged_authors(items: Iterable[dict]) -> dict[int, tuple[dict, set[int]]]:
    """Authors of merged PRs among `items`, with the PR numbers."""
    found: dict[int, tuple[dict, set[int]]] = {}
    for item in items:
        user = item.get("user") or {}
        if not (item.get("pull_request") or {}).get("merged_at"):
            continue
        if _is_bot(user) or "id" not in user:
            continue
        found.setdefault(int(user["id"]), (user, set()))[1].add(int(item["number"]))
    return found


def fixes(item: dict) -> set[int]:
    return {int(n) for n in FIXES.findall(item.get("body") or "")}


def assess(
    items: list[dict], issues: Callable[[int], dict | None], config: VouchConfig
) -> tuple[list[int], list[str]]:
    """A contributor's merged PR numbers, and why triage would hold off.

    `items` are the issues and PRs this person opened; `issues` looks up any
    issue by number. Merge count alone is easy to farm, so it counts for
    nothing here. Triage asks instead whether the work came steadily over
    time, at a human pace, and on problems that were not staged for it:

    - short: merges in fewer than `min_merge_weeks` weeks, or under
      `min_span_days` from first to last
    - burst: more than `burst_per_day` issues and PRs opened in one day
    - prebaked: most PRs fix an issue the same person opened minutes before
    - fast-follow: most PRs land on someone else's issue within hours of it
      being opened
    - rejected: more PRs closed unmerged than merged
    """
    prs = [i for i in items if i.get("pull_request")]
    merged = [p for p in prs if p["pull_request"].get("merged_at")]
    numbers = sorted(int(p["number"]) for p in merged)
    reasons: list[str] = []
    if merged:
        stamps = [_parse(p["pull_request"]["merged_at"]) for p in merged]
        weeks = {stamp.isocalendar()[:2] for stamp in stamps}
        if (
            len(weeks) < config.min_merge_weeks
            or (max(stamps) - min(stamps)).days < config.min_span_days
        ):
            reasons.append("short")
    days: dict[str, int] = {}
    for item in items:
        day = str(item.get("created_at", ""))[:10]
        days[day] = days.get(day, 0) + 1
    if days and max(days.values()) > config.burst_per_day:
        reasons.append("burst")
    prebaked = fast = 0
    for pr in prs:
        opened = _parse(pr["created_at"])
        for number in fixes(pr):
            issue = issues(number)
            if not issue or issue.get("pull_request"):
                continue
            lag = (opened - _parse(issue["created_at"])).total_seconds()
            mine = (issue.get("user") or {}).get("login") == (pr.get("user") or {}).get(
                "login"
            )
            if mine and 0 <= lag <= config.prebaked_minutes * 60:
                prebaked += 1
                break
            if not mine and 0 <= lag <= config.fast_follow_hours * 3600:
                fast += 1
                break
    if prs and prebaked * 2 > len(prs):
        reasons.append("prebaked")
    if prs and fast >= 2 and fast * 2 >= len(prs):
        reasons.append("fast-follow")
    closed = sum(
        1
        for p in prs
        if p.get("state") == "closed" and not p["pull_request"].get("merged_at")
    )
    if closed > len(merged):
        reasons.append("rejected")
    return numbers, reasons


def decide(
    graph: Graph,
    user: dict,
    numbers: list[int],
    reasons: list[str],
    maintainer: bool,
    now: datetime,
) -> dict | None:
    """The triage edge to write for `user`, or None when it already says so.

    Write access is a vouch on its own. Otherwise a clean record is a vouch,
    and anything else is a hold that names the reasons; a hold counts as no
    edge, but lets the digest and the next run see that triage looked.
    """
    edge = graph.triage_edge(int(user["id"])) or {}
    kind: Kind = "vouch" if maintainer or not reasons else "hold"
    if maintainer:
        reason = "write access"
    elif reasons:
        reason = "held: " + ", ".join(reasons)
    else:
        reason = f"{len(numbers)} merged PR{'s' if len(numbers) != 1 else ''}"
    if (
        edge.get("kind") == kind
        and edge.get("reason") == reason
        and set(numbers) <= set(edge.get("merged", []))
    ):
        return None
    return vouch_record(
        user,
        kind,
        TRIAGE,
        now,
        reason=reason,
        evidence=numbers,
        merged=set(numbers) | set(edge.get("merged", [])),
    )


def needs_assessment(
    graph: Graph, authors: dict[int, tuple[dict, set[int]]]
) -> list[dict]:
    """Merge authors triage hasn't looked at since their newest merge."""
    due = []
    for person_id, (user, numbers) in sorted(authors.items()):
        edge = graph.triage_edge(person_id) or {}
        if not numbers <= set(edge.get("merged", [])):
            due.append(user)
    return due


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


def contributor_items(read: Callable[[str], object], login: str) -> list[dict]:
    """Every issue and PR `login` opened here, from GitHub search.

    Search rejects a query that doesn't say issues or PRs, so each is its own.
    """
    found: list[dict] = []
    for kind in ("is:issue", "is:pr"):
        for page in range(1, 11):
            query = quote(f"repo:{REPOSITORY} {kind} author:{login}")
            result = read(f"search/issues?q={query}&per_page=100&page={page}")
            batch = result.get("items", []) if isinstance(result, dict) else []
            found.extend(batch)
            if len(batch) < 100:
                break
    return found


def sync_graph(
    store: Store,
    items: list[dict],
    read: Callable[[str], object],
    config: VouchConfig,
    now: datetime,
    complete: bool = False,
    maintainer: Callable[[str], bool] = lambda login: False,
) -> dict:
    """Assess authors with new merges, then refresh some affiliations.

    `complete` means `items` is the whole repository (a backfill), so every
    author is assessed from it without another request. Otherwise each author
    due is looked up with search, up to `assess_per_run`.
    """
    if isinstance(store, SpaceStore):
        store.ensure_space()
    graph = Graph(store.records())
    due = needs_assessment(graph, merged_authors(items))
    by_number = {int(i["number"]): i for i in items}

    def issue(number: int) -> dict | None:
        if number in by_number or complete:
            return by_number.get(number)
        found = read(f"repos/{REPOSITORY}/issues/{number}")
        by_number[number] = found if isinstance(found, dict) else {}
        return by_number[number] or None

    if complete:
        mine: dict[str, list[dict]] = {}
        for item in items:
            mine.setdefault((item.get("user") or {}).get("login", ""), []).append(item)
    else:
        due = due[: config.assess_per_run]
    written = held = 0
    for user in due:
        try:
            own = (
                mine.get(user["login"], [])
                if complete
                else contributor_items(read, user["login"])
            )
            numbers, reasons = assess(own, issue, config)
            record = decide(
                graph, user, numbers, reasons, maintainer(user["login"]), now
            )
        except (RuntimeError, ValueError, KeyError, OSError, httpx.HTTPError):
            continue
        if record:
            store.append(VOUCH, record)
            written += 1
            held += record["kind"] == "hold"
    if written:
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
    return {
        "assessed": len(due),
        "vouches": written - held,
        "holds": held,
        "affiliations_refreshed": refreshed,
    }


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
