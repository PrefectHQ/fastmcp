"""Keep the triage memory index current.

`triage-memory-sync` runs every 30 minutes and re-indexes items GitHub reports
as updated in the last two hours, so a missed run costs nothing. `backfill=True`
indexes the whole repository (about four minutes). Both do nothing while
`fastmcp_triage_config.memory.mode` is "off".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import httpx
from prefect import flow, get_run_logger
from prefect.variables import Variable

from flows.agentic_github import GitHub
from flows.agentic_labels import CONFIG_VARIABLE
from flows.agentic_memory import (
    REPOSITORY,
    SECRET_BLOCK,
    Index,
    changed_since,
    lookback,
    memory_config,
    sync,
)
from flows.agentic_stack_runtime import secret_sync
from flows.agentic_vouch import PASSWORD_BLOCK, SpaceStore, sync_graph, vouch_config
from flows.agentic_work import PdsError

MAX_PAGES = 400  # 40,000 rows: all items, or all comments, with headroom


def all_pages(github: GitHub, path: str) -> list[dict]:
    result: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        separator = "&" if "?" in path else "?"
        batch = github.request(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Expected a GitHub collection")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError("GitHub collection exceeded the memory bound")


@flow(name="triage-memory-sync", log_prints=False)
def triage_memory_sync(lookback_hours: int = 2, backfill: bool = False) -> int:
    logger = get_run_logger()
    config = memory_config(Variable.get(CONFIG_VARIABLE, default={}))
    if config.mode == "off":
        logger.info("Triage memory is off in %s", CONFIG_VARIABLE)
        return 0
    github = GitHub(secret_sync("fastmcp-triage-gh-token"))
    index = Index(config, secret_sync(SECRET_BLOCK))
    since = datetime(2000, 1, 1, tzinfo=UTC) if backfill else lookback(lookback_hours)
    items = changed_since(lambda path: all_pages(github, path), since)
    if backfill:
        # One repository-wide listing instead of a request per item, which
        # would exhaust the token's hourly budget on a full backfill.
        threads: dict[int, list[dict]] = defaultdict(list)
        for comment in all_pages(
            github, f"repos/{REPOSITORY}/issues/comments?sort=created&direction=asc"
        ):
            threads[int(comment["issue_url"].rsplit("/", 1)[1])].append(comment)
        comments_of = threads.__getitem__
    else:

        def comments_of(number: int) -> list[dict]:
            return all_pages(github, f"repos/{REPOSITORY}/issues/{number}/comments")

    written = sync(items, comments_of, index.replace)
    logger.info("Indexed %d items updated since %s", written, since.isoformat())
    update_contributors(items, github, logger)
    return written


def update_contributors(items: list[dict], github: GitHub, logger) -> None:
    """Feed the same items to the contributor graph (flows/agentic_vouch.py).

    The graph is a side job of this sync: if its space is unreachable, the
    memory index is still current and the next run catches up.
    """
    config = vouch_config(Variable.get(CONFIG_VARIABLE, default={}))
    if config.mode == "off":
        return
    try:
        store = SpaceStore(config, lambda: secret_sync(PASSWORD_BLOCK))
        result = sync_graph(store, items, github.request, config, datetime.now(UTC))
    except (PdsError, httpx.HTTPError, ValueError, OSError) as error:
        logger.warning("Contributor graph not updated: %s", type(error).__name__)
        return
    logger.info("Contributor graph: %s", result)
