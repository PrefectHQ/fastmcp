"""SecureMCP Quickstart — a fully trust-native MCP server.

Demonstrates:
    1. Creating security components (registry, marketplace, provenance, etc.)
    2. Registering a tool with a security manifest
    3. Publishing the tool to the marketplace
    4. Mounting HTTP API endpoints for the React dashboard
    5. Running the server

Run::

    uv run python examples/securemcp/quickstart.py

Then visit:
    http://localhost:8000/security/health
    http://localhost:8000/security/dashboard
    http://localhost:8000/security/marketplace
    http://localhost:8000/security/trust
    http://localhost:8000/security/compliance
"""

from __future__ import annotations

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.certification import SecurityManifest  # noqa: F401
from fastmcp.server.security.compliance.reports import ComplianceReporter
from fastmcp.server.security.dashboard.snapshot import SecurityDashboard
from fastmcp.server.security.federation.crl import CertificateRevocationList
from fastmcp.server.security.federation.federation import TrustFederation
from fastmcp.server.security.gateway.tool_marketplace import (
    ToolCategory,
    ToolMarketplace,
)
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.provenance.records import ProvenanceAction
from fastmcp.server.security.registry.registry import TrustRegistry
from securemcp import SecureMCP
from securemcp.http import SecurityAPI

# ── 1. Create an MCP server ─────────────────────────────────────

server = SecureMCP("securemcp-demo")

# ── 2. Create security components ───────────────────────────────

registry = TrustRegistry()
event_bus = SecurityEventBus()
marketplace = ToolMarketplace(trust_registry=registry, event_bus=event_bus)
provenance = ProvenanceLedger(event_bus=event_bus)
federation = TrustFederation(event_bus=event_bus)
crl = CertificateRevocationList()
compliance = ComplianceReporter()

# ── 3. Register a tool with a security manifest ─────────────────

registry.register(
    "weather-lookup",
    tool_version="1.2.0",
    author="demo-author",
    tags={"weather", "api"},
)

# ── 4. Publish to the marketplace ───────────────────────────────

marketplace.publish(
    "weather-lookup",
    display_name="Weather Lookup",
    description="Look up current weather for a city.",
    version="1.2.0",
    author="demo-author",
    categories={ToolCategory.UTILITY},
    tags={"weather", "api"},
    tool_license="MIT",
)

# ── 5. Record a provenance event ────────────────────────────────

provenance.record(
    action=ProvenanceAction.TOOL_CALLED,
    actor_id="agent-1",
    resource_id="weather-lookup",
    metadata={"city": "San Francisco"},
)

# ── 6. Wire up the dashboard and HTTP API ────────────────────────

dashboard = SecurityDashboard(
    registry=registry,
    marketplace=marketplace,
    federation=federation,
    crl=crl,
    compliance_reporter=compliance,
    event_bus=event_bus,
)

api = SecurityAPI(
    dashboard=dashboard,
    marketplace=marketplace,
    registry=registry,
    compliance_reporter=compliance,
    provenance_ledger=provenance,
    federation=federation,
    crl=crl,
    event_bus=event_bus,
)
server.mount_security_api(api=api)


# ── 7. Add a regular MCP tool ───────────────────────────────────


@server.tool()
def weather_lookup(city: str) -> str:
    """Look up current weather for a city."""
    return f"Weather in {city}: Sunny, 72°F"


# ── 8. Run ──────────────────────────────────────────────────────

if __name__ == "__main__":
    server.run(transport="streamable-http")
