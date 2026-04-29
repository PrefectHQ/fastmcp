"""
FastMCP Lore Context Server

Wraps the Lore Context REST API as MCP tools, giving any MCP-compatible
client access to governed agent memory (search, write, read, list, forget).

Configuration (environment variables):
    LORE_API_URL   – Base URL of the Lore Context API (default: http://127.0.0.1:3000)
    LORE_API_KEY   – API key for authentication (required)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LORE_API_URL = os.environ.get("LORE_API_URL", "http://127.0.0.1:3000").rstrip("/")
LORE_API_KEY = os.environ.get("LORE_API_KEY", "")

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("Lore Context Server")


def _headers() -> dict[str, str]:
    """Build auth headers for every Lore REST call."""
    return {
        "Authorization": f"Bearer {LORE_API_KEY}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
async def memory_search(
    query: str,
    project_id: str | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Search Lore memories by semantic similarity.

    Args:
        query: Natural-language search query.
        project_id: Optional project scope filter.
        top_k: Maximum number of hits to return (1-100).

    Returns:
        A dict with a ``hits`` key containing matching memory records.
    """
    body: dict[str, Any] = {"query": query}
    if project_id is not None:
        body["project_id"] = project_id
    if top_k is not None:
        body["top_k"] = top_k

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LORE_API_URL}/v1/memory/search",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def memory_write(
    content: str,
    scope: str = "project",
    project_id: str | None = None,
    memory_type: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Write a new governed memory into Lore.

    The memory may enter a review queue depending on governance policy.

    Args:
        content: The memory content to store.
        scope: Memory scope – one of "user", "project", "repo", "team", "org".
        project_id: Required when scope is "project".
        memory_type: Optional memory type label.
        confidence: Optional confidence score (0.0-1.0).

    Returns:
        A dict with ``memory`` (the created record) and ``reviewRequired``.
    """
    body: dict[str, Any] = {"content": content, "scope": scope}
    if project_id is not None:
        body["project_id"] = project_id
    if memory_type is not None:
        body["memory_type"] = memory_type
    if confidence is not None:
        body["confidence"] = confidence

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LORE_API_URL}/v1/memory/write",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def memory_get(memory_id: str) -> dict[str, Any]:
    """Fetch a single memory record by its id.

    Args:
        memory_id: The unique memory identifier.

    Returns:
        A dict with a ``memory`` key containing the full record.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LORE_API_URL}/v1/memory/{memory_id}",
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def memory_list(
    project_id: str | None = None,
    scope: str | None = None,
    status: str | None = None,
    memory_type: str | None = None,
    q: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List visible memories with optional filters.

    Args:
        project_id: Filter by project (required for scoped API keys).
        scope: Filter by memory scope.
        status: Filter by lifecycle status.
        memory_type: Filter by memory type.
        q: Substring filter on memory content.
        limit: Maximum number of memories to return.

    Returns:
        A dict with a ``memories`` key containing the list of records.
    """
    params: dict[str, Any] = {}
    if project_id is not None:
        params["project_id"] = project_id
    if scope is not None:
        params["scope"] = scope
    if status is not None:
        params["status"] = status
    if memory_type is not None:
        params["memory_type"] = memory_type
    if q is not None:
        params["q"] = q
    if limit is not None:
        params["limit"] = limit

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LORE_API_URL}/v1/memory/list",
            params=params,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool
async def memory_forget(
    reason: str,
    memory_ids: list[str] | None = None,
    query: str | None = None,
    project_id: str | None = None,
    hard_delete: bool = False,
) -> dict[str, Any]:
    """Forget (soft-delete or hard-delete) memories.

    Provide either ``memory_ids`` to target specific memories, or ``query``
    to match and forget semantically.  Admin role required for hard deletes.

    Args:
        reason: Explanation of why the memories are being forgotten (min 8 chars).
        memory_ids: Explicit list of memory ids to delete.
        query: Semantic query to select memories for deletion.
        project_id: Optional project scope.
        hard_delete: If True, permanently erase; otherwise soft-delete.

    Returns:
        A dict with ``deleted`` count, ``memoryIds``, and ``hardDelete`` flag.
    """
    body: dict[str, Any] = {"reason": reason, "hard_delete": hard_delete}
    if memory_ids is not None:
        body["memory_ids"] = memory_ids
    if query is not None:
        body["query"] = query
    if project_id is not None:
        body["project_id"] = project_id

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LORE_API_URL}/v1/memory/forget",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
