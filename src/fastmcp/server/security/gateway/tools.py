"""MCP tool definitions for the SecureMCP API Gateway.

These functions are designed to be registered as MCP tools on a
FastMCP server, exposing audit and marketplace functionality
to connected agents.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.security.gateway.audit import AuditAPI
from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.gateway.models import (
    AuditQuery,
    AuditQueryType,
    ServerCapability,
    TrustLevel,
)


def create_audit_tools(api: AuditAPI) -> dict[str, Any]:
    """Create tool functions for the audit API.

    Returns a dict of {tool_name: callable} that can be registered
    on a FastMCP server.

    Example::

        tools = create_audit_tools(audit_api)
        for name, fn in tools.items():
            mcp.tool(name=name)(fn)
    """

    async def query_provenance(
        actor_id: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query the provenance ledger for audit records.

        Args:
            actor_id: Filter by actor.
            resource_id: Filter by resource.
            limit: Max results.
        """
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.PROVENANCE,
                actor_id=actor_id,
                resource_id=resource_id,
                limit=limit,
            )
        )
        return {
            "total_count": result.total_count,
            "records": result.records,
            "has_more": result.has_more,
        }

    async def query_drift_events(
        actor_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query behavioral drift events.

        Args:
            actor_id: Filter by actor.
            limit: Max results.
        """
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.DRIFT,
                actor_id=actor_id,
                limit=limit,
            )
        )
        return {
            "total_count": result.total_count,
            "records": result.records,
            "has_more": result.has_more,
        }

    async def query_consent_log(
        actor_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query consent audit log.

        Args:
            actor_id: Filter by actor involved.
            limit: Max results.
        """
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.CONSENT,
                actor_id=actor_id,
                limit=limit,
            )
        )
        return {
            "total_count": result.total_count,
            "records": result.records,
            "has_more": result.has_more,
        }

    async def query_policy_status() -> dict[str, Any]:
        """Get policy engine status and evaluation summary."""
        result = api.query(
            AuditQuery(query_type=AuditQueryType.POLICY)
        )
        return {
            "records": result.records,
            "metadata": result.metadata,
        }

    async def get_security_status() -> dict[str, Any]:
        """Get overall security subsystem health status."""
        status = api.get_status()
        return status.to_dict()

    return {
        "securemcp_query_provenance": query_provenance,
        "securemcp_query_drift": query_drift_events,
        "securemcp_query_consent": query_consent_log,
        "securemcp_query_policy": query_policy_status,
        "securemcp_security_status": get_security_status,
    }


def create_marketplace_tools(marketplace: Marketplace) -> dict[str, Any]:
    """Create tool functions for the marketplace.

    Returns a dict of {tool_name: callable} that can be registered
    on a FastMCP server.
    """

    async def search_servers(
        capabilities: list[str] | None = None,
        min_trust_level: str | None = None,
        tags: list[str] | None = None,
        healthy_only: bool = False,
        name_contains: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search the SecureMCP marketplace for servers.

        Args:
            capabilities: Required capability names.
            min_trust_level: Minimum trust level.
            tags: Required tags (any match).
            healthy_only: Only return healthy servers.
            name_contains: Search server names.
            limit: Max results.
        """
        caps = None
        if capabilities:
            caps = set()
            for c in capabilities:
                try:
                    caps.add(ServerCapability(c))
                except ValueError:
                    pass

        trust = None
        if min_trust_level:
            try:
                trust = TrustLevel(min_trust_level)
            except ValueError:
                pass

        results = marketplace.search(
            capabilities=caps,
            min_trust_level=trust,
            tags=set(tags) if tags else None,
            healthy_only=healthy_only,
            name_contains=name_contains,
            limit=limit,
        )
        return {
            "count": len(results),
            "servers": [r.to_dict() for r in results],
        }

    async def get_server_info(server_id: str) -> dict[str, Any]:
        """Get detailed information about a marketplace server.

        Args:
            server_id: The server to look up.
        """
        reg = marketplace.get(server_id)
        if reg is None:
            return {"error": f"Server not found: {server_id}"}
        return reg.to_dict()

    async def marketplace_stats() -> dict[str, Any]:
        """Get marketplace statistics."""
        servers = marketplace.get_all_servers()
        caps_count: dict[str, int] = {}
        trust_count: dict[str, int] = {}

        for s in servers:
            for cap in s.capabilities:
                caps_count[cap.value] = caps_count.get(cap.value, 0) + 1
            trust_count[s.trust_level.value] = (
                trust_count.get(s.trust_level.value, 0) + 1
            )

        return {
            "total_servers": len(servers),
            "capabilities_distribution": caps_count,
            "trust_level_distribution": trust_count,
            "healthy_servers": sum(1 for s in servers if s.is_healthy()),
        }

    return {
        "securemcp_search_servers": search_servers,
        "securemcp_server_info": get_server_info,
        "securemcp_marketplace_stats": marketplace_stats,
    }
