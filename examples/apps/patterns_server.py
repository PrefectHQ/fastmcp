"""Patterns showcase — every Prefab pattern from the docs in one server.

A runnable collection of the patterns from https://gofastmcp.com/apps/patterns.
Each tool demonstrates a different Prefab UI pattern: charts, tables, forms,
status displays, conditional content, tabs, and accordions.

Usage:
    uv run python patterns_server.py              # HTTP (port 8000)
    uv run python patterns_server.py --stdio       # stdio for MCP clients
"""

from __future__ import annotations

from prefab_ui.actions import ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Accordion,
    AccordionItem,
    Alert,
    AreaChart,
    Badge,
    BarChart,
    Button,
    Card,
    CardContent,
    ChartSeries,
    Column,
    DataTable,
    DataTableColumn,
    ForEach,
    Form,
    Grid,
    Heading,
    If,
    Input,
    Muted,
    PieChart,
    Progress,
    Row,
    Select,
    Separator,
    Switch,
    Tab,
    Tabs,
    Text,
    Textarea,
)

from fastmcp import FastMCP

mcp = FastMCP("Patterns Showcase")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

QUARTERLY_DATA = [
    {"quarter": "Q1", "revenue": 42000, "costs": 28000},
    {"quarter": "Q2", "revenue": 51000, "costs": 31000},
    {"quarter": "Q3", "revenue": 47000, "costs": 29000},
    {"quarter": "Q4", "revenue": 63000, "costs": 35000},
]

DAILY_USAGE = [
    {"date": f"Feb {d}", "requests": v}
    for d, v in zip(
        range(1, 11),
        [1200, 1350, 980, 1500, 1420, 1680, 1550, 1700, 1450, 1600],
    )
]

TICKETS = [
    {"category": "Bug", "count": 23},
    {"category": "Feature", "count": 15},
    {"category": "Docs", "count": 8},
    {"category": "Infra", "count": 12},
]

EMPLOYEES = [
    {
        "name": "Alice Chen",
        "department": "Engineering",
        "role": "Staff Engineer",
        "location": "San Francisco",
    },
    {
        "name": "Bob Martinez",
        "department": "Design",
        "role": "Lead Designer",
        "location": "New York",
    },
    {
        "name": "Carol Johnson",
        "department": "Engineering",
        "role": "Senior Engineer",
        "location": "London",
    },
    {
        "name": "David Kim",
        "department": "Product",
        "role": "Product Manager",
        "location": "San Francisco",
    },
    {
        "name": "Eva Müller",
        "department": "Engineering",
        "role": "Engineer",
        "location": "Berlin",
    },
    {
        "name": "Frank Okafor",
        "department": "Data Science",
        "role": "Senior Analyst",
        "location": "Lagos",
    },
    {
        "name": "Grace Liu",
        "department": "Engineering",
        "role": "Junior Engineer",
        "location": "Singapore",
    },
    {
        "name": "Hassan Ali",
        "department": "Design",
        "role": "Senior Designer",
        "location": "Dubai",
    },
]

SERVICES = [
    {
        "name": "API Gateway",
        "status": "healthy",
        "ok": True,
        "latency_ms": 12,
        "uptime_pct": 99.9,
    },
    {
        "name": "Database",
        "status": "healthy",
        "ok": True,
        "latency_ms": 3,
        "uptime_pct": 99.99,
    },
    {
        "name": "Cache",
        "status": "degraded",
        "ok": False,
        "latency_ms": 45,
        "uptime_pct": 98.2,
    },
    {
        "name": "Queue",
        "status": "healthy",
        "ok": True,
        "latency_ms": 8,
        "uptime_pct": 99.8,
    },
]

ENDPOINTS = [
    {
        "path": "/api/users",
        "status": 200,
        "healthy": True,
        "avg_ms": 45,
        "p99_ms": 120,
        "uptime_pct": 99.9,
    },
    {
        "path": "/api/orders",
        "status": 200,
        "healthy": True,
        "avg_ms": 82,
        "p99_ms": 250,
        "uptime_pct": 99.7,
    },
    {
        "path": "/api/search",
        "status": 200,
        "healthy": True,
        "avg_ms": 150,
        "p99_ms": 500,
        "uptime_pct": 99.5,
    },
    {
        "path": "/api/webhooks",
        "status": 503,
        "healthy": False,
        "avg_ms": 2000,
        "p99_ms": 5000,
        "uptime_pct": 95.1,
    },
]

PROJECT = {
    "name": "FastMCP v3",
    "description": "Next generation MCP framework with Apps support.",
    "status": "Active",
    "created_at": "2025-01-15",
    "members": [
        {"name": "Alice Chen", "role": "Lead"},
        {"name": "Bob Martinez", "role": "Design"},
        {"name": "Carol Johnson", "role": "Backend"},
    ],
    "activity": [
        {
            "timestamp": "2 hours ago",
            "message": "Merged PR #342: Add Prefab UI integration",
        },
        {
            "timestamp": "5 hours ago",
            "message": "Opened issue #345: CORS convenience API",
        },
        {"timestamp": "1 day ago", "message": "Released v3.0.1"},
    ],
}

# In-memory contact store for the form demo
_contacts: list[dict] = [
    {"name": "Zaphod Beeblebrox", "email": "zaphod@galaxy.gov", "category": "Partner"},
]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def quarterly_revenue(year: int = 2025) -> PrefabApp:
    """Show quarterly revenue as a bar chart."""
    with Column(gap=4, css_class="p-6") as view:
        Heading(f"{year} Revenue vs Costs")
        BarChart(
            data=QUARTERLY_DATA,
            series=[
                ChartSeries(data_key="revenue", label="Revenue"),
                ChartSeries(data_key="costs", label="Costs"),
            ],
            x_axis="quarter",
            show_legend=True,
        )

    return PrefabApp(view=view)


@mcp.tool(app=True)
def usage_trend() -> PrefabApp:
    """Show API usage over time as an area chart."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("API Usage (10 Days)")
        AreaChart(
            data=DAILY_USAGE,
            series=[ChartSeries(data_key="requests", label="Requests")],
            x_axis="date",
            curve="smooth",
            height=250,
        )

    return PrefabApp(view=view)


@mcp.tool(app=True)
def ticket_breakdown() -> PrefabApp:
    """Show open tickets by category as a donut chart."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("Open Tickets")
        PieChart(
            data=TICKETS,
            data_key="count",
            name_key="category",
            show_legend=True,
            inner_radius=60,
        )

    return PrefabApp(view=view)


# ---------------------------------------------------------------------------
# Data Tables
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def employee_directory() -> PrefabApp:
    """Show a searchable, sortable employee directory."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("Employee Directory")
        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name", sortable=True),
                DataTableColumn(key="department", header="Department", sortable=True),
                DataTableColumn(key="role", header="Role"),
                DataTableColumn(key="location", header="Office", sortable=True),
            ],
            rows=EMPLOYEES,
            searchable=True,
            paginated=True,
            page_size=15,
        )

    return PrefabApp(view=view)


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def contact_form() -> PrefabApp:
    """Show a form to create a new contact, with a live contact list below."""
    with Column(gap=6, css_class="p-6") as view:
        Heading("Contacts")

        with ForEach("contacts"):
            with Row(gap=2, align="center"):
                Text("{{ name }}", css_class="font-medium")
                Muted("{{ email }}")
                Badge("{{ category }}")

        Separator()

        Heading("Add Contact", level=3)
        with Form(
            on_submit=CallTool(
                "save_contact",
                result_key="contacts",
                on_success=ShowToast("Contact saved!", variant="success"),
                on_error=ShowToast("{{ $error }}", variant="error"),
            )
        ):
            Input(name="name", label="Full Name", required=True)
            Input(name="email", label="Email", input_type="email", required=True)
            Select(
                name="category",
                label="Category",
                options=["Customer", "Vendor", "Partner", "Other"],
            )
            Textarea(name="notes", label="Notes", placeholder="Optional notes...")
            Button("Save Contact")

    return PrefabApp(view=view, state={"contacts": list(_contacts)})


@mcp.tool
def save_contact(
    name: str,
    email: str,
    category: str = "Other",
    notes: str = "",
) -> list[dict]:
    """Save a new contact and return the updated list."""
    contact = {"name": name, "email": email, "category": category, "notes": notes}
    _contacts.append(contact)
    return list(_contacts)


# ---------------------------------------------------------------------------
# Status Displays
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def system_status() -> PrefabApp:
    """Show current system health."""
    all_ok = all(s["ok"] for s in SERVICES)

    with Column(gap=4, css_class="p-6") as view:
        with Row(gap=2, align="center"):
            Heading("System Status")
            Badge(
                "All Healthy" if all_ok else "Degraded",
                variant="success" if all_ok else "destructive",
            )

        Separator()

        with Grid(columns=2, gap=4):
            for svc in SERVICES:
                with Card():
                    with CardContent():
                        with Row(gap=2, align="center"):
                            Text(svc["name"], css_class="font-medium")
                            Badge(
                                svc["status"],
                                variant="success" if svc["ok"] else "destructive",
                            )
                        Muted(f"Response: {svc['latency_ms']}ms")
                        Progress(value=svc["uptime_pct"])

    return PrefabApp(view=view)


# ---------------------------------------------------------------------------
# Conditional Content
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def feature_flags() -> PrefabApp:
    """Toggle feature flags with live preview."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("Feature Flags")

        Switch(name="dark_mode", label="Dark Mode")
        Switch(name="beta_features", label="Beta Features")

        Separator()

        with If("{{ dark_mode }}"):
            Alert(title="Dark mode enabled", description="UI will use dark theme.")
        with If("{{ beta_features }}"):
            Alert(
                title="Beta features active",
                description="Experimental features are now visible.",
                variant="warning",
            )

    return PrefabApp(view=view, state={"dark_mode": False, "beta_features": False})


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def project_overview() -> PrefabApp:
    """Show project details organized in tabs."""
    with Column(gap=4, css_class="p-6") as view:
        Heading(PROJECT["name"])

        with Tabs():
            with Tab("Overview"):
                Text(PROJECT["description"])
                with Row(gap=4):
                    Badge(PROJECT["status"])
                    Muted(f"Created: {PROJECT['created_at']}")

            with Tab("Members"):
                DataTable(
                    columns=[
                        DataTableColumn(key="name", header="Name", sortable=True),
                        DataTableColumn(key="role", header="Role"),
                    ],
                    rows=PROJECT["members"],
                )

            with Tab("Activity"):
                with ForEach("activity"):
                    with Row(gap=2):
                        Muted("{{ timestamp }}")
                        Text("{{ message }}")

    return PrefabApp(view=view, state={"activity": PROJECT["activity"]})


# ---------------------------------------------------------------------------
# Accordion
# ---------------------------------------------------------------------------


@mcp.tool(app=True)
def api_health() -> PrefabApp:
    """Show health details for each API endpoint."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("API Health")

        with Accordion(multiple=True):
            for ep in ENDPOINTS:
                with AccordionItem(ep["path"]):
                    with Row(gap=4):
                        Badge(
                            f"{ep['status']}",
                            variant="success" if ep["healthy"] else "destructive",
                        )
                        Text(f"Avg: {ep['avg_ms']}ms")
                        Text(f"P99: {ep['p99_ms']}ms")
                    Progress(value=ep["uptime_pct"])

    return PrefabApp(view=view)


if __name__ == "__main__":
    mcp.run()
