"""Consent graph engine for federated access-rights evaluation.

Manages a directed graph of consent relationships and evaluates
access queries by traversing consent paths, checking conditions,
and respecting delegation chains.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.consent.models import (
    ConsentDecision,
    ConsentEdge,
    ConsentNode,
    ConsentQuery,
    ConsentStatus,
    NodeType,
)
from fastmcp.server.security.storage.backend import StorageBackend

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus

logger = logging.getLogger(__name__)


def _consent_node_to_dict(node: Any) -> dict[str, Any]:
    from fastmcp.server.security.storage.serialization import consent_node_to_dict

    return consent_node_to_dict(node)


def _consent_node_from_dict(data: dict[str, Any]) -> Any:
    from fastmcp.server.security.storage.serialization import consent_node_from_dict

    return consent_node_from_dict(data)


def _consent_edge_to_dict(edge: Any) -> dict[str, Any]:
    from fastmcp.server.security.storage.serialization import consent_edge_to_dict

    return consent_edge_to_dict(edge)


def _consent_edge_from_dict(data: dict[str, Any]) -> Any:
    from fastmcp.server.security.storage.serialization import consent_edge_from_dict

    return consent_edge_from_dict(data)


class ConsentGraph:
    """A directed graph of consent relationships.

    Nodes represent agents, resources, scopes, or groups.
    Edges represent consent grants with scopes, conditions, and delegation.

    The graph supports:
    - Direct consent lookups (source → target with scope)
    - Delegation chain traversal (A→B→C if B can delegate)
    - Conditional consent (evaluated at query time)
    - Group-based consent (expand group membership)
    - Consent revocation and expiry

    Example::

        graph = ConsentGraph()

        # Add nodes
        graph.add_node(ConsentNode("owner", NodeType.AGENT, "Resource Owner"))
        graph.add_node(ConsentNode("agent-1", NodeType.AGENT, "Agent 1"))
        graph.add_node(ConsentNode("data", NodeType.RESOURCE, "Data Resource"))

        # Grant consent
        graph.grant(
            source_id="owner",
            target_id="agent-1",
            scopes={"read", "execute"},
        )

        # Check consent
        decision = graph.evaluate(ConsentQuery(
            source_id="owner",
            target_id="agent-1",
            scope="read",
        ))
        assert decision.granted

    Args:
        graph_id: Identifier for this consent graph instance.
    """

    def __init__(
        self,
        graph_id: str = "default",
        *,
        backend: StorageBackend | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self.graph_id = graph_id
        self._backend = backend
        self._event_bus = event_bus
        self._nodes: dict[str, ConsentNode] = {}
        # Edges indexed: source_id → list[ConsentEdge]
        self._outgoing: dict[str, list[ConsentEdge]] = defaultdict(list)
        # Reverse index: target_id → list[ConsentEdge]
        self._incoming: dict[str, list[ConsentEdge]] = defaultdict(list)
        # Edge lookup by ID
        self._edges: dict[str, ConsentEdge] = {}
        # Group membership: group_id → set of member node_ids
        self._groups: dict[str, set[str]] = defaultdict(set)
        # Audit log
        self._audit_log: list[dict[str, Any]] = []

        # Load persisted state
        if self._backend is not None:
            self._load_from_backend()

    def _load_from_backend(self) -> None:
        """Load graph state from backend and rebuild indices."""
        if self._backend is None:
            return
        data = self._backend.load_consent_graph(self.graph_id)
        # Load nodes
        for node_id, node_data in data.get("nodes", {}).items():
            node = _consent_node_from_dict(node_data)
            self._nodes[node_id] = node
        # Load edges and rebuild outgoing/incoming indices
        for edge_id, edge_data in data.get("edges", {}).items():
            edge = _consent_edge_from_dict(edge_data)
            self._edges[edge_id] = edge
            self._outgoing[edge.source_id].append(edge)
            self._incoming[edge.target_id].append(edge)
        # Load groups
        for group_id, members in data.get("groups", {}).items():
            self._groups[group_id] = set(members)
        # Load audit log
        self._audit_log = list(data.get("audit_log", []))

    # ── Node management ──────────────────────────────────────────────

    def add_node(self, node: ConsentNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.node_id] = node
        if node.node_type == NodeType.GROUP:
            if node.node_id not in self._groups:
                self._groups[node.node_id] = set()
        if self._backend is not None:
            self._backend.save_consent_node(
                self.graph_id, node.node_id, _consent_node_to_dict(node)
            )

    def get_node(self, node_id: str) -> ConsentNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and all its edges."""
        if node_id not in self._nodes:
            return False

        # Remove all outgoing edges
        for edge in list(self._outgoing.get(node_id, [])):
            self._remove_edge(edge)
        # Remove all incoming edges
        for edge in list(self._incoming.get(node_id, [])):
            self._remove_edge(edge)

        # Remove from groups
        for gid, members in self._groups.items():
            if node_id in members:
                members.discard(node_id)
                if self._backend is not None:
                    self._backend.save_consent_group(
                        self.graph_id, gid, sorted(members)
                    )
        if node_id in self._groups:
            self._groups.pop(node_id, None)
            if self._backend is not None:
                self._backend.remove_consent_group(self.graph_id, node_id)

        del self._nodes[node_id]
        if self._backend is not None:
            self._backend.remove_consent_node(self.graph_id, node_id)
        return True

    @property
    def node_count(self) -> int:
        """Number of nodes in the graph."""
        return len(self._nodes)

    # ── Group management ─────────────────────────────────────────────

    def add_to_group(self, group_id: str, member_id: str) -> None:
        """Add a node to a group."""
        self._groups[group_id].add(member_id)
        if self._backend is not None:
            self._backend.save_consent_group(
                self.graph_id, group_id, sorted(self._groups[group_id])
            )

    def remove_from_group(self, group_id: str, member_id: str) -> bool:
        """Remove a node from a group."""
        if group_id in self._groups:
            self._groups[group_id].discard(member_id)
            if self._backend is not None:
                self._backend.save_consent_group(
                    self.graph_id, group_id, sorted(self._groups[group_id])
                )
            return True
        return False

    def get_group_members(self, group_id: str) -> set[str]:
        """Get all members of a group."""
        return set(self._groups.get(group_id, set()))

    def get_groups_for_node(self, node_id: str) -> set[str]:
        """Get all groups a node belongs to."""
        return {gid for gid, members in self._groups.items() if node_id in members}

    # ── Consent grant/revoke ─────────────────────────────────────────

    def grant(
        self,
        source_id: str,
        target_id: str,
        scopes: set[str],
        *,
        conditions: list[Any] | None = None,
        expires_at: datetime | None = None,
        granted_by: str = "",
        delegatable: bool = False,
        max_delegation_depth: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ConsentEdge:
        """Grant consent from source to target.

        Args:
            source_id: The node granting access.
            target_id: The node receiving access.
            scopes: Set of granted scopes.
            conditions: Conditions for consent validity.
            expires_at: When consent expires.
            granted_by: Who authorized this grant.
            delegatable: Whether target can delegate.
            max_delegation_depth: Max levels of delegation.
            metadata: Additional context.

        Returns:
            The created ConsentEdge.
        """
        edge = ConsentEdge(
            source_id=source_id,
            target_id=target_id,
            scopes=set(scopes),
            conditions=list(conditions or []),
            expires_at=expires_at,
            granted_by=granted_by or source_id,
            delegatable=delegatable,
            max_delegation_depth=max_delegation_depth,
            metadata=metadata or {},
        )
        self._add_edge(edge)

        # Persist edge
        if self._backend is not None:
            self._backend.save_consent_edge(
                self.graph_id, edge.edge_id, _consent_edge_to_dict(edge)
            )

        audit_entry = {
            "action": "grant",
            "edge_id": edge.edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "scopes": list(scopes),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_consent_audit(self.graph_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CONSENT_GRANTED,
                    severity=AlertSeverity.INFO,
                    layer="consent",
                    message=f"Consent granted: {source_id} → {target_id}",
                    actor_id=granted_by or source_id,
                    resource_id=edge.edge_id,
                    data={
                        "source_id": source_id,
                        "target_id": target_id,
                        "scopes": list(scopes),
                    },
                )
            )

        logger.info(
            "Consent granted: %s → %s (scopes: %s)",
            source_id,
            target_id,
            scopes,
        )
        return edge

    def delegate(
        self,
        parent_edge_id: str,
        new_target_id: str,
        scopes: set[str] | None = None,
    ) -> ConsentEdge | None:
        """Delegate consent from an existing edge to a new target.

        The new edge inherits constraints from the parent but may
        restrict scopes further. Cannot expand scopes beyond parent.

        Args:
            parent_edge_id: The edge to delegate from.
            new_target_id: The new target node.
            scopes: Scopes to delegate (must be subset of parent).

        Returns:
            The new delegated edge, or None if delegation is not allowed.
        """
        parent = self._edges.get(parent_edge_id)
        if parent is None:
            return None

        if not parent.is_valid():
            return None

        if not parent.can_delegate():
            return None

        # Scopes must be subset of parent
        effective_scopes = scopes or parent.scopes
        if not effective_scopes.issubset(parent.scopes):
            return None

        edge = ConsentEdge(
            source_id=parent.source_id,
            target_id=new_target_id,
            scopes=effective_scopes,
            conditions=list(parent.conditions),
            expires_at=parent.expires_at,
            granted_by=parent.target_id,
            delegatable=parent.delegatable,
            max_delegation_depth=parent.max_delegation_depth,
            delegation_depth=parent.delegation_depth + 1,
            parent_edge_id=parent_edge_id,
        )

        # Check depth limit
        if edge.max_delegation_depth > 0:
            if edge.delegation_depth > edge.max_delegation_depth:
                return None

        self._add_edge(edge)

        # Persist delegated edge
        if self._backend is not None:
            self._backend.save_consent_edge(
                self.graph_id, edge.edge_id, _consent_edge_to_dict(edge)
            )

        audit_entry = {
            "action": "delegate",
            "edge_id": edge.edge_id,
            "parent_edge_id": parent_edge_id,
            "new_target_id": new_target_id,
            "scopes": list(effective_scopes),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_consent_audit(self.graph_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CONSENT_DELEGATED,
                    severity=AlertSeverity.INFO,
                    layer="consent",
                    message=f"Consent delegated to {new_target_id}",
                    actor_id=parent.target_id,
                    resource_id=edge.edge_id,
                    data={
                        "parent_edge_id": parent_edge_id,
                        "new_target_id": new_target_id,
                        "scopes": list(effective_scopes),
                        "delegation_depth": edge.delegation_depth,
                    },
                )
            )

        return edge

    def revoke(self, edge_id: str) -> bool:
        """Revoke a consent edge and all its delegated children."""
        edge = self._edges.get(edge_id)
        if edge is None:
            return False

        edge.status = ConsentStatus.REVOKED

        # Persist updated edge status
        if self._backend is not None:
            self._backend.save_consent_edge(
                self.graph_id, edge_id, _consent_edge_to_dict(edge)
            )

        # Cascade revocation to delegated edges
        for child_edge in list(self._edges.values()):
            if child_edge.parent_edge_id == edge_id:
                self.revoke(child_edge.edge_id)

        audit_entry = {
            "action": "revoke",
            "edge_id": edge_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log.append(audit_entry)
        if self._backend is not None:
            self._backend.append_consent_audit(self.graph_id, audit_entry)

        # Emit alert event
        if self._event_bus is not None:
            from fastmcp.server.security.alerts.models import (
                AlertSeverity,
                SecurityEvent,
                SecurityEventType,
            )

            self._event_bus.emit(
                SecurityEvent(
                    event_type=SecurityEventType.CONSENT_REVOKED,
                    severity=AlertSeverity.WARNING,
                    layer="consent",
                    message=f"Consent revoked: edge {edge_id}",
                    resource_id=edge_id,
                    data={
                        "source_id": edge.source_id,
                        "target_id": edge.target_id,
                    },
                )
            )

        logger.info("Consent revoked: edge %s", edge_id)
        return True

    def revoke_all(self, source_id: str, target_id: str) -> int:
        """Revoke all consent from source to target.

        Returns the number of edges revoked.
        """
        count = 0
        for edge in list(self._outgoing.get(source_id, [])):
            if edge.target_id == target_id and edge.status == ConsentStatus.ACTIVE:
                self.revoke(edge.edge_id)
                count += 1
        return count

    # ── Consent evaluation ───────────────────────────────────────────

    def evaluate(self, query: ConsentQuery) -> ConsentDecision:
        """Evaluate whether consent exists for a query.

        Checks direct edges, group membership, and delegation chains.

        Args:
            query: The consent query to evaluate.

        Returns:
            ConsentDecision with granted status and path.
        """
        now = datetime.now(timezone.utc)

        # 1. Check direct edges from source to target
        direct = self._check_direct(
            query.source_id, query.target_id, query.scope, query.context, now
        )
        if direct is not None:
            return ConsentDecision(
                granted=True,
                path=[direct],
                reason=f"Direct consent: {query.source_id} → {query.target_id}",
            )

        # 2. Check if target is in a group that has consent
        for group_id in self.get_groups_for_node(query.target_id):
            group_edge = self._check_direct(
                query.source_id, group_id, query.scope, query.context, now
            )
            if group_edge is not None:
                return ConsentDecision(
                    granted=True,
                    path=[group_edge],
                    reason=(
                        f"Group consent: {query.target_id} is member of "
                        f"{group_id} which has consent from {query.source_id}"
                    ),
                )

        # 3. Check if source is in a group that granted consent to target
        for group_id in self.get_groups_for_node(query.source_id):
            group_edge = self._check_direct(
                group_id, query.target_id, query.scope, query.context, now
            )
            if group_edge is not None:
                return ConsentDecision(
                    granted=True,
                    path=[group_edge],
                    reason=(
                        f"Source group consent: {query.source_id} is member of "
                        f"{group_id} which granted consent to {query.target_id}"
                    ),
                )

        # 4. Check delegation chains (BFS)
        if query.allow_delegation:
            chain = self._find_delegation_chain(
                query.source_id, query.target_id, query.scope, query.context, now
            )
            if chain:
                return ConsentDecision(
                    granted=True,
                    path=chain,
                    reason=(
                        f"Delegated consent chain: "
                        f"{' → '.join(e.target_id for e in chain)}"
                    ),
                )

        return ConsentDecision(
            granted=False,
            reason=(
                f"No consent found: {query.source_id} → {query.target_id} "
                f"(scope: {query.scope})"
            ),
        )

    # ── Query helpers ────────────────────────────────────────────────

    def get_consents_for(self, target_id: str) -> list[ConsentEdge]:
        """Get all active consent edges targeting a node."""
        return [e for e in self._incoming.get(target_id, []) if e.is_valid()]

    def get_consents_from(self, source_id: str) -> list[ConsentEdge]:
        """Get all active consent edges from a source node."""
        return [e for e in self._outgoing.get(source_id, []) if e.is_valid()]

    def get_edge(self, edge_id: str) -> ConsentEdge | None:
        """Get a consent edge by ID."""
        return self._edges.get(edge_id)

    @property
    def edge_count(self) -> int:
        """Total number of edges in the graph."""
        return len(self._edges)

    def get_audit_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent audit log entries."""
        return list(reversed(self._audit_log[-limit:]))

    # ── Internal helpers ─────────────────────────────────────────────

    def _add_edge(self, edge: ConsentEdge) -> None:
        """Add an edge to all indices."""
        self._edges[edge.edge_id] = edge
        self._outgoing[edge.source_id].append(edge)
        self._incoming[edge.target_id].append(edge)

    def _remove_edge(self, edge: ConsentEdge) -> None:
        """Remove an edge from all indices."""
        self._edges.pop(edge.edge_id, None)
        out = self._outgoing.get(edge.source_id, [])
        if edge in out:
            out.remove(edge)
        inc = self._incoming.get(edge.target_id, [])
        if edge in inc:
            inc.remove(edge)

    def _check_direct(
        self,
        source_id: str,
        target_id: str,
        scope: str,
        context: dict[str, Any],
        now: datetime,
    ) -> ConsentEdge | None:
        """Check for a direct valid consent edge."""
        for edge in self._outgoing.get(source_id, []):
            if edge.target_id != target_id:
                continue
            if not edge.is_valid(now):
                continue
            if scope and scope not in edge.scopes:
                continue
            if not edge.check_conditions(context):
                continue
            return edge
        return None

    def _find_delegation_chain(
        self,
        source_id: str,
        target_id: str,
        scope: str,
        context: dict[str, Any],
        now: datetime,
        max_depth: int = 10,
    ) -> list[ConsentEdge] | None:
        """BFS to find a delegation chain from source to target.

        Looks for paths where intermediate nodes have delegatable consent.
        """
        # Start from all direct grants from source
        queue: list[tuple[str, list[ConsentEdge]]] = []

        for edge in self._outgoing.get(source_id, []):
            if not edge.is_valid(now):
                continue
            if scope and scope not in edge.scopes:
                continue
            if not edge.check_conditions(context):
                continue
            if edge.target_id == target_id:
                return [edge]
            if edge.delegatable:
                queue.append((edge.target_id, [edge]))

        visited: set[str] = {source_id}

        while queue:
            current_id, path = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            if len(path) >= max_depth:
                continue

            for edge in self._outgoing.get(current_id, []):
                if not edge.is_valid(now):
                    continue
                if scope and scope not in edge.scopes:
                    continue
                if not edge.check_conditions(context):
                    continue

                new_path = path + [edge]
                if edge.target_id == target_id:
                    return new_path
                if edge.target_id not in visited and edge.delegatable:
                    queue.append((edge.target_id, new_path))

        return None
