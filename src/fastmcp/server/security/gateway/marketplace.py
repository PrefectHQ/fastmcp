"""Marketplace registry for SecureMCP servers.

Enables discovery of trust-capable MCP servers, their capabilities,
and trust levels. Servers register themselves and can be queried
by capability, trust level, or tags.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.gateway.models import (
    ServerCapability,
    ServerRegistration,
    TrustLevel,
)
from fastmcp.server.security.storage.backend import StorageBackend

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus

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

    def __init__(
        self,
        marketplace_id: str = "default",
        *,
        backend: StorageBackend | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self.marketplace_id = marketplace_id
        self._backend = backend
        self._event_bus = event_bus
        self._servers: dict[str, ServerRegistration] = {}
        self._audit_log: list[dict[str, Any]] = []

        # Load persisted state
        if self._backend is not None:
            self._load_from_backend()

    def _load_from_backend(self) -> None:
        """Load marketplace state from backend."""
        if self._backend is None:
            return
        from fastmcp.server.security.storage.serialization import (
            server_registration_from_dict,
        )

        data = self._backend.load_marketplace(self.marketplace_id)
        for server_id, server_data in data.get("servers", {}).items():
            self._servers[server_id] = server_registration_from_dict(server_data)
        self._audit_log = list(data.get("audit_log", []))

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

        # Persist registration
        if self._backend is not None:
            from fastmcp.server.security.storage.serialization import (
                server_registration_to_dict,
            )

            self._backend.save_server_registration(
                self.marketplace_id, reg.server_id, server_registration_to_dict(reg)
            )

        audit_entry = {
            "action": "register",
            "server_id": reg.server_id,
            "name": name,
            "endpoint": endpoint,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_marketplace_audit(self.marketplace_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.SERVER_REGISTERED,
                    severity=AlertSeverity.INFO,
                    layer="gateway",
                    message=f"Server registered: {name}",
                    resource_id=reg.server_id,
                    data={"name": name, "endpoint": endpoint},
                )
            )

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

        # Remove from backend
        if self._backend is not None:
            self._backend.remove_server_registration(self.marketplace_id, server_id)

        audit_entry = {
            "action": "unregister",
            "server_id": server_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_marketplace_audit(self.marketplace_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.SERVER_UNREGISTERED,
                    severity=AlertSeverity.WARNING,
                    layer="gateway",
                    message=f"Server unregistered: {server_id}",
                    resource_id=server_id,
                )
            )

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
        # Persist updated heartbeat
        if self._backend is not None:
            from fastmcp.server.security.storage.serialization import (
                server_registration_to_dict,
            )

            self._backend.save_server_registration(
                self.marketplace_id, server_id, server_registration_to_dict(reg)
            )
        return True

    def update_trust_level(self, server_id: str, trust_level: TrustLevel) -> bool:
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

        # Persist updated trust level
        if self._backend is not None:
            from fastmcp.server.security.storage.serialization import (
                server_registration_to_dict,
            )

            self._backend.save_server_registration(
                self.marketplace_id, server_id, server_registration_to_dict(reg)
            )

        audit_entry = {
            "action": "trust_update",
            "server_id": server_id,
            "old_level": old_level.value,
            "new_level": trust_level.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_marketplace_audit(self.marketplace_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TRUST_CHANGED,
                    severity=AlertSeverity.WARNING,
                    layer="gateway",
                    message=f"Trust level changed: {old_level.value} → {trust_level.value}",
                    resource_id=server_id,
                    data={
                        "old_level": old_level.value,
                        "new_level": trust_level.value,
                    },
                )
            )

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
