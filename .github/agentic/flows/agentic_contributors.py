"""A maintainer's vouch, denounce or retract, written to the contributor graph.

Run `contributor-vouch` with the maintainer's own login as `by`. It checks that
`by` has write access to the repository and that `login` is a real GitHub
account, then appends one record. Nothing is posted anywhere public.

This is a flow rather than a tool on the triage MCP server because triage
agents can call that server's tools, and only people decide denounces.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prefect import flow, get_run_logger
from prefect.variables import Variable

from flows.agentic_github import GitHub, can_write
from flows.agentic_labels import CONFIG_VARIABLE
from flows.agentic_stack_runtime import secret_sync
from flows.agentic_vouch import (
    PASSWORD_BLOCK,
    TRIAGE,
    VOUCH,
    Kind,
    SpaceStore,
    Store,
    vouch_config,
    vouch_record,
)


def maintainer_vouch(
    store: Store,
    read,
    login: str,
    kind: Kind,
    by: str,
    reason: str,
    evidence: list[int],
    now: datetime,
) -> dict:
    if by.lower() == TRIAGE:
        raise ValueError("`by` must be a maintainer's login")
    if not can_write(read, by):
        raise ValueError(f"{by} has no write access to the repository")
    user = read(f"users/{login}")
    if not isinstance(user, dict) or "id" not in user:
        raise ValueError(f"No GitHub account {login}")
    record = vouch_record(user, kind, by, now, reason=reason, evidence=evidence)
    store.append(VOUCH, record)
    return record


@flow(name="contributor-vouch", log_prints=False)
def contributor_vouch(
    login: str,
    kind: Kind,
    by: str,
    reason: str = "",
    evidence: list[int] | None = None,
) -> dict:
    config = vouch_config(Variable.get(CONFIG_VARIABLE, default={}))
    store = SpaceStore(config, lambda: secret_sync(PASSWORD_BLOCK))
    store.ensure_space()
    github = GitHub(secret_sync("fastmcp-triage-gh-token"))
    record = maintainer_vouch(
        store,
        github.request,
        login,
        kind,
        by,
        reason,
        evidence or [],
        datetime.now(UTC),
    )
    get_run_logger().info("%s: %s by %s", login, kind, by)
    return record
