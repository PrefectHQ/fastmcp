"""In-memory storage backend for SecureMCP.

Preserves the current behavior of all security layers exactly.
All data is stored in Python dicts/lists and lost on restart.
This is the default backend when no persistence is configured.
"""

from __future__ import annotations

from typing import Any


class MemoryBackend:
    """In-memory storage backend.

    Stores all data in process memory. This is the default backend
    and preserves the existing behavior of all security components.

    Example::

        from fastmcp.server.security.storage import MemoryBackend

        backend = MemoryBackend()
    """

    def __init__(self) -> None:
        # Provenance
        self._provenance: dict[str, list[dict[str, Any]]] = {}
        # Exchange log
        self._exchange: dict[str, list[dict[str, Any]]] = {}
        # Contracts
        self._contracts: dict[str, dict[str, dict[str, Any]]] = {}
        # Baselines: {analyzer_id: {actor_id: {metric_name: data}}}
        self._baselines: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        # Drift history
        self._drift: dict[str, list[dict[str, Any]]] = {}
        # Escalation history
        self._escalations: dict[str, list[dict[str, Any]]] = {}
        # Consent graph: {graph_id: {nodes, edges, groups, audit_log}}
        self._consent: dict[str, dict[str, Any]] = {}
        # Marketplace: {mp_id: {servers, audit_log}}
        self._marketplace: dict[str, dict[str, Any]] = {}
        # Tool marketplace: {mp_id: {listings, installs, reviews}}
        self._tool_marketplace: dict[str, dict[str, Any]] = {}

    # ── Provenance ────────────────────────────────────────────────

    def append_provenance_record(
        self, ledger_id: str, record_data: dict[str, Any]
    ) -> None:
        self._provenance.setdefault(ledger_id, []).append(record_data)

    def load_provenance_records(self, ledger_id: str) -> list[dict[str, Any]]:
        return list(self._provenance.get(ledger_id, []))

    # ── Exchange Log ──────────────────────────────────────────────

    def append_exchange_entry(self, log_id: str, entry_data: dict[str, Any]) -> None:
        self._exchange.setdefault(log_id, []).append(entry_data)

    def load_exchange_entries(self, log_id: str) -> list[dict[str, Any]]:
        return list(self._exchange.get(log_id, []))

    # ── Contracts ─────────────────────────────────────────────────

    def save_contract(
        self, broker_id: str, contract_id: str, data: dict[str, Any]
    ) -> None:
        self._contracts.setdefault(broker_id, {})[contract_id] = data

    def remove_contract(self, broker_id: str, contract_id: str) -> None:
        if broker_id in self._contracts:
            self._contracts[broker_id].pop(contract_id, None)

    def load_contracts(self, broker_id: str) -> dict[str, dict[str, Any]]:
        return dict(self._contracts.get(broker_id, {}))

    # ── Baselines ─────────────────────────────────────────────────

    def save_baseline(
        self,
        analyzer_id: str,
        actor_id: str,
        metric_name: str,
        data: dict[str, Any],
    ) -> None:
        store = self._baselines.setdefault(analyzer_id, {})
        store.setdefault(actor_id, {})[metric_name] = data

    def remove_baseline(
        self, analyzer_id: str, actor_id: str, metric_name: str | None = None
    ) -> None:
        store = self._baselines.get(analyzer_id, {})
        if metric_name is None:
            store.pop(actor_id, None)
        elif actor_id in store:
            store[actor_id].pop(metric_name, None)

    def load_baselines(self, analyzer_id: str) -> dict[str, dict[str, dict[str, Any]]]:
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for actor_id, metrics in self._baselines.get(analyzer_id, {}).items():
            result[actor_id] = dict(metrics)
        return result

    # ── Drift History ─────────────────────────────────────────────

    def append_drift_event(self, analyzer_id: str, event_data: dict[str, Any]) -> None:
        self._drift.setdefault(analyzer_id, []).append(event_data)

    def load_drift_history(self, analyzer_id: str) -> list[dict[str, Any]]:
        return list(self._drift.get(analyzer_id, []))

    # ── Escalation History ────────────────────────────────────────

    def append_escalation(self, engine_id: str, data: dict[str, Any]) -> None:
        self._escalations.setdefault(engine_id, []).append(data)

    def load_escalations(self, engine_id: str) -> list[dict[str, Any]]:
        return list(self._escalations.get(engine_id, []))

    # ── Consent Graph ─────────────────────────────────────────────

    def _ensure_consent(self, graph_id: str) -> dict[str, Any]:
        if graph_id not in self._consent:
            self._consent[graph_id] = {
                "nodes": {},
                "edges": {},
                "groups": {},
                "audit_log": [],
            }
        return self._consent[graph_id]

    def save_consent_node(
        self, graph_id: str, node_id: str, data: dict[str, Any]
    ) -> None:
        self._ensure_consent(graph_id)["nodes"][node_id] = data

    def remove_consent_node(self, graph_id: str, node_id: str) -> None:
        store = self._ensure_consent(graph_id)
        store["nodes"].pop(node_id, None)

    def save_consent_edge(
        self, graph_id: str, edge_id: str, data: dict[str, Any]
    ) -> None:
        self._ensure_consent(graph_id)["edges"][edge_id] = data

    def remove_consent_edge(self, graph_id: str, edge_id: str) -> None:
        store = self._ensure_consent(graph_id)
        store["edges"].pop(edge_id, None)

    def save_consent_group(
        self, graph_id: str, group_id: str, members: list[str]
    ) -> None:
        self._ensure_consent(graph_id)["groups"][group_id] = members

    def remove_consent_group(self, graph_id: str, group_id: str) -> None:
        store = self._ensure_consent(graph_id)
        store["groups"].pop(group_id, None)

    def append_consent_audit(self, graph_id: str, entry: dict[str, Any]) -> None:
        self._ensure_consent(graph_id)["audit_log"].append(entry)

    def load_consent_graph(self, graph_id: str) -> dict[str, Any]:
        store = self._ensure_consent(graph_id)
        return {
            "nodes": dict(store["nodes"]),
            "edges": dict(store["edges"]),
            "groups": {k: list(v) for k, v in store["groups"].items()},
            "audit_log": list(store["audit_log"]),
        }

    # ── Marketplace ───────────────────────────────────────────────

    def _ensure_marketplace(self, mp_id: str) -> dict[str, Any]:
        if mp_id not in self._marketplace:
            self._marketplace[mp_id] = {
                "servers": {},
                "audit_log": [],
            }
        return self._marketplace[mp_id]

    def save_server_registration(
        self, mp_id: str, server_id: str, data: dict[str, Any]
    ) -> None:
        self._ensure_marketplace(mp_id)["servers"][server_id] = data

    def remove_server_registration(self, mp_id: str, server_id: str) -> None:
        store = self._ensure_marketplace(mp_id)
        store["servers"].pop(server_id, None)

    def append_marketplace_audit(self, mp_id: str, entry: dict[str, Any]) -> None:
        self._ensure_marketplace(mp_id)["audit_log"].append(entry)

    def load_marketplace(self, mp_id: str) -> dict[str, Any]:
        store = self._ensure_marketplace(mp_id)
        return {
            "servers": dict(store["servers"]),
            "audit_log": list(store["audit_log"]),
        }

    # ── Tool Marketplace ──────────────────────────────────────────

    def _ensure_tool_marketplace(self, mp_id: str) -> dict[str, Any]:
        if mp_id not in self._tool_marketplace:
            self._tool_marketplace[mp_id] = {
                "listings": {},
                "installs": {},
                "reviews": {},
            }
        return self._tool_marketplace[mp_id]

    def save_tool_listing(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        self._ensure_tool_marketplace(mp_id)["listings"][listing_id] = data

    def remove_tool_listing(self, mp_id: str, listing_id: str) -> None:
        store = self._ensure_tool_marketplace(mp_id)
        store["listings"].pop(listing_id, None)
        store["installs"].pop(listing_id, None)
        store["reviews"].pop(listing_id, None)

    def append_tool_install(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        store = self._ensure_tool_marketplace(mp_id)
        store["installs"].setdefault(listing_id, []).append(data)

    def append_tool_review(
        self, mp_id: str, listing_id: str, data: dict[str, Any]
    ) -> None:
        store = self._ensure_tool_marketplace(mp_id)
        store["reviews"].setdefault(listing_id, []).append(data)

    def load_tool_marketplace(self, mp_id: str) -> dict[str, Any]:
        store = self._ensure_tool_marketplace(mp_id)
        return {
            "listings": dict(store["listings"]),
            "installs": {
                listing_id: list(installs)
                for listing_id, installs in store["installs"].items()
            },
            "reviews": {
                listing_id: list(reviews)
                for listing_id, reviews in store["reviews"].items()
            },
        }

    # ── Policy Versioning ────────────────────────────────────────

    def save_policy_version(self, policy_set_id: str, data: dict[str, Any]) -> None:
        if not hasattr(self, "_policy_versions"):
            self._policy_versions: dict[str, dict[str, Any]] = {}
        self._policy_versions[policy_set_id] = data

    def load_policy_versions(self, policy_set_id: str) -> dict[str, Any] | None:
        if not hasattr(self, "_policy_versions"):
            self._policy_versions: dict[str, dict[str, Any]] = {}
        return self._policy_versions.get(policy_set_id)
