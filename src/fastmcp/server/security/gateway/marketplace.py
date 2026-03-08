"""Marketplace registry for SecureMCP servers.

Enables discovery of trust-capable MCP servers, their capabilities,
and trust levels. Servers register themselves and can be queried
by capability, trust level, or tags.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.gateway.models import (
    ServerCapability,
    ServerRegistration,
    TrustLevel,
)

logger = logging.getLogger(__name__)


class Marketplace:
    """Registry for discovering SecureMCP-capable servers.

    Maintains a directory of registered servers with their
    capabilities, trust levels, and health status.

    Example::

        marketplace = Marketplace()

        # Register a server
        reg = marketplace.register(
            name="My Secure Server",
            endpoint="https://my-server.example.com",
            capabilities={
                ServerCapability.POLICY_ENGINE,
                ServerCapability.PROVENANCE_LEDGER,
            },
        )

        # Discover servers
        results = marketplace.search(
            capabilities={ServerCapability.PROVENANCE_LEDGER},
        )

    Args:
        marketplace_id: Identifier for this marketplace instance.
    """

    def __init__(self, marketplace_id: str = "default") -> None:
        self.marketplace_id = marketplace_id
        self._servers: dict[str, ServerRegistration] = {}
        self._audit_log: list[dict[str, Any]] = []

    def register(
        self,
        name: str,
        endpoint: str,
        *,
        capabilities: set[ServerCapability] | None = None,
        trust_level: TrustLevel = TrustLevel.UNVERIFIED,
        version: str = "",
        description: str = "",
        tags: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
        server_id: str | None = None,
    ) -> ServerRegistration:
        """Register a new server in the marketplace.

        Args:
            name: Human-readable name.
            endpoint: Connection endpoint.
            capabilities: Security features supported.
            trust_level: Current trust certification.
            version: Server version string.
            description: What this server provides.
            tags: Searchable tags.
            metadata: Additional properties.
            server_id: Optional explicit ID (auto-generated if None).

        Returns:
            The created ServerRegistration.
        """
        reg = ServerRegistration(
            name=name,
            endpoint=endpoint,
            capabilities=capabilities or set(),
            trust_level=trust_level,
            version=version,
            description=description,
            tags=tags or set(),
            metadata=metadata or {},
        )
        if server_id is not None:
            reg.server_id = server_id

        self._servers[reg.server_id] = reg

        self._audit_log.append({
            "action": "register",
            "server_id": reg.server_id,
            "name": name,
            "endpoint": endpoint,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("Server registered: %s (%s)", name, reg.server_id)
        return reg

    def unregister(self, server_id: str) -> bool:
        """Remove a server from the marketplace.

        Args:
            server_id: The server to remove.

        Returns:
            True if the server was found and removed.
        """
        if server_id not in self._servers:
            return False

        del self._servers[server_id]

        self._audit_log.append({
            "action": "unregister",
            "server_id": server_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return True

    def heartbeat(self, server_id: str) -> bool:
        """Update the last heartbeat time for a server.

        Args:
            server_id: The server reporting health.

        Returns:
            True if the server was found.
        """
        reg = self._servers.get(server_id)
        if reg is None:
            return False
        reg.last_heartbeat = datetime.now(timezone.utc)
        return True

    def update_trust_level(
        self, server_id: str, trust_level: TrustLevel
    ) -> bool:
        """Update a server's trust level.

        Args:
            server_id: The server to update.
            trust_level: The new trust level.

        Returns:
            True if the server was found.
        """
        reg = self._servers.get(server_id)
        if reg is None:
            return False

        old_level = reg.trust_level
        reg.trust_level = trust_level

        self._audit_log.append({
            "action": "trust_update",
            "server_id": server_id,
            "old_level": old_level.value,
            "new_level": trust_level.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return True

    def get(self, server_id: str) -> ServerRegistration | None:
        """Get a server by ID."""
        return self._servers.get(server_id)

    def search(
        self,
        *,
        capabilities: set[ServerCapability] | None = None,
        trust_level: TrustLevel | None = None,
        min_trust_level: TrustLevel | None = None,
        tags: set[str] | None = None,
        healthy_only: bool = False,
        name_contains: str | None = None,
        limit: int = 100,
    ) -> list[ServerRegistration]:
        """Search for servers matching criteria.

        All filters are AND-combined. Omitted filters match everything.

        Args:
            capabilities: Required capabilities (all must be present).
            trust_level: Exact trust level match.
            min_trust_level: Minimum trust level.
            tags: Required tags (any must be present).
            healthy_only: Only return healthy servers.
            name_contains: Case-insensitive name search.
            limit: Maximum results.

        Returns:
            List of matching server registrations.
        """
        trust_order = list(TrustLevel)
        results: list[ServerRegistration] = []

        for reg in self._servers.values():
            # Capability filter
            if capabilities:
                if not capabilities.issubset(reg.capabilities):
                    continue

            # Trust level exact
            if trust_level is not None and reg.trust_level != trust_level:
                continue

            # Min trust level
            if min_trust_level is not None:
                if trust_order.index(reg.trust_level) < trust_order.index(
                    min_trust_level
                ):
                    continue

            # Tags (any match)
            if tags and not tags.intersection(reg.tags):
                continue

            # Health check
            if healthy_only and not reg.is_healthy():
                continue

            # Name search
            if name_contains and name_contains.lower() not in reg.name.lower():
                continue

            results.append(reg)
            if len(results) >= limit:
                break

        return results

    @property
    def server_count(self) -> int:
        """Total registered servers."""
        return len(self._servers)

    def get_audit_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent marketplace audit log entries."""
        return list(reversed(self._audit_log[-limit:]))

    def get_all_servers(self) -> list[ServerRegistration]:
        """Get all registered servers."""
        return list(self._servers.values())
