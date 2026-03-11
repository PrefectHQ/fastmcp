"""Storage backend protocol for SecureMCP.

Defines the interface that all storage backends must implement.
Data flows as JSON-safe dicts — serialization is handled by the
calling components using the serialization module.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Sync storage backend for SecureMCP security layers.

    All methods accept and return JSON-safe dicts. The calling
    component is responsible for serialization/deserialization
    via the ``storage.serialization`` module.

    Append-only methods (``append_*``) are used for ledgers and logs
    where data is never modified after creation. Save/remove methods
    are used for mutable state.
    """

    # ── Provenance Ledger ─────────────────────────────────────────

    def append_provenance_record(
        self, ledger_id: str, record_data: dict[str, Any]
    ) -> None:
        """Persist a single provenance record (append-only)."""
        ...

    def load_provenance_records(self, ledger_id: str) -> list[dict[str, Any]]:
        """Load all provenance records for a ledger, in order."""
        ...

    # ── Exchange Log ──────────────────────────────────────────────

    def append_exchange_entry(
        self, log_id: str, entry_data: dict[str, Any]
    ) -> None:
        """Persist a single exchange log entry (append-only)."""
        ...

    def load_exchange_entries(self, log_id: str) -> list[dict[str, Any]]:
        """Load all exchange log entries for a log, in order."""
        ...

    # ── Contracts ─────────────────────────────────────────────────

    def save_contract(
        self, broker_id: str, contract_id: str, data: dict[str, Any]
    ) -> None:
        """Persist or update an active contract."""
        ...

    def remove_contract(self, broker_id: str, contract_id: str) -> None:
        """Remove a contract (e.g., after revocation)."""
        ...

    def load_contracts(self, broker_id: str) -> dict[str, dict[str, Any]]:
        """Load all contracts for a broker. Returns {contract_id: data}."""
        ...

    # ── Behavioral Baselines ──────────────────────────────────────

    def save_baseline(
        self,
        analyzer_id: str,
        actor_id: str,
        metric_name: str,
        data: dict[str, Any],
    ) -> None:
        """Persist or update a behavioral baseline."""
        ...

    def remove_baseline(
        self, analyzer_id: str, actor_id: str, metric_name: str | None = None
    ) -> None:
        """Remove baselines for an actor. If metric_name is None, remove all."""
        ...

    def load_baselines(
        self, analyzer_id: str
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Load all baselines. Returns {actor_id: {metric_name: data}}."""
        ...

    # ── Drift History ─────────────────────────────────────────────

    def append_drift_event(
        self, analyzer_id: str, event_data: dict[str, Any]
    ) -> None:
        """Persist a drift event (append-only)."""
        ...

    def load_drift_history(self, analyzer_id: str) -> list[dict[str, Any]]:
        """Load all drift events for an analyzer, in order."""
        ...

    # ── Escalation History ────────────────────────────────────────

    def append_escalation(
        self, engine_id: str, data: dict[str, Any]
    ) -> None:
        """Persist an escalation record (append-only)."""
        ...

    def load_escalations(self, engine_id: str) -> list[dict[str, Any]]:
        """Load all escalation records for an engine, in order."""
        ...

    # ── Consent Graph ─────────────────────────────────────────────

    def save_consent_node(
        self, graph_id: str, node_id: str, data: dict[str, Any]
    ) -> None:
        """Persist or update a consent node."""
        ...

    def remove_consent_node(self, graph_id: str, node_id: str) -> None:
        """Remove a consent node."""
        ...

    def save_consent_edge(
        self, graph_id: str, edge_id: str, data: dict[str, Any]
    ) -> None:
        """Persist or update a consent edge."""
        ...

    def remove_consent_edge(self, graph_id: str, edge_id: str) -> None:
        """Remove a consent edge."""
        ...

    def save_consent_group(
        self, graph_id: str, group_id: str, members: list[str]
    ) -> None:
        """Persist or update a consent group's membership."""
        ...

    def remove_consent_group(self, graph_id: str, group_id: str) -> None:
        """Remove a consent group."""
        ...

    def append_consent_audit(
        self, graph_id: str, entry: dict[str, Any]
    ) -> None:
        """Persist a consent audit log entry (append-only)."""
        ...

    def load_consent_graph(self, graph_id: str) -> dict[str, Any]:
        """Load consent graph state.

        Returns::

            {
                "nodes": {node_id: data, ...},
                "edges": {edge_id: data, ...},
                "groups": {group_id: [member_id, ...], ...},
                "audit_log": [entry, ...],
            }
        """
        ...

    # ── Marketplace ───────────────────────────────────────────────

    def save_server_registration(
        self, mp_id: str, server_id: str, data: dict[str, Any]
    ) -> None:
        """Persist or update a server registration."""
        ...

    def remove_server_registration(
        self, mp_id: str, server_id: str
    ) -> None:
        """Remove a server registration."""
        ...

    def append_marketplace_audit(
        self, mp_id: str, entry: dict[str, Any]
    ) -> None:
        """Persist a marketplace audit log entry (append-only)."""
        ...

    def load_marketplace(self, mp_id: str) -> dict[str, Any]:
        """Load marketplace state.

        Returns::

            {
                "servers": {server_id: data, ...},
                "audit_log": [entry, ...],
            }
        """
        ...

    # ── Policy Versioning ────────────────────────────────────────

    def save_policy_version(
        self, policy_set_id: str, data: dict[str, Any]
    ) -> None:
        """Persist the full version history for a policy set."""
        ...

    def load_policy_versions(
        self, policy_set_id: str
    ) -> dict[str, Any] | None:
        """Load version history for a policy set. Returns None if not found."""
        ...

    # ── Tool Marketplace ──────────────────────────────────────────

    def save_tool_listing(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        """Persist or update a tool listing."""
        ...

    def remove_tool_listing(self, mp_id: str, listing_id: str) -> None:
        """Remove a tool listing."""
        ...

    def append_tool_install(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        """Persist a tool install record (append-only)."""
        ...

    def append_tool_review(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        """Persist a tool review (append-only)."""
        ...

    def load_tool_marketplace(self, mp_id: str) -> dict[str, Any]:
        """Load tool marketplace state.

        Returns::

            {
                "listings": {listing_id: data, ...},
                "installs": {listing_id: [record, ...], ...},
            }
        """
        ...
