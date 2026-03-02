"""DataTable MCP App — interactive, sortable data views with Prefab.

Demonstrates `fastmcp[apps]` with Prefab UI components:
- `app=True` for automatic renderer wiring
- `PrefabApp` with `DataTable` for rich tabular views
- Searchable, sortable, paginated tables
- Layout composition with `Column`, `Heading`, `Text`, and `Badge`

Usage:
    uv run python datatable_server.py              # HTTP (port 8000)
    uv run python datatable_server.py --stdio       # stdio for MCP clients
"""

from __future__ import annotations

from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Column,
    DataTable,
    DataTableColumn,
    Heading,
    Muted,
    Row,
)

from fastmcp import FastMCP

mcp = FastMCP("Team Directory")

EMPLOYEES = [
    {
        "name": "Alice Chen",
        "role": "Engineering",
        "level": "Senior",
        "location": "San Francisco",
        "status": "active",
    },
    {
        "name": "Bob Martinez",
        "role": "Design",
        "level": "Lead",
        "location": "New York",
        "status": "active",
    },
    {
        "name": "Carol Johnson",
        "role": "Engineering",
        "level": "Staff",
        "location": "London",
        "status": "active",
    },
    {
        "name": "David Kim",
        "role": "Product",
        "level": "Senior",
        "location": "San Francisco",
        "status": "away",
    },
    {
        "name": "Eva Müller",
        "role": "Engineering",
        "level": "Mid",
        "location": "Berlin",
        "status": "active",
    },
    {
        "name": "Frank Okafor",
        "role": "Data Science",
        "level": "Senior",
        "location": "Lagos",
        "status": "active",
    },
    {
        "name": "Grace Liu",
        "role": "Engineering",
        "level": "Junior",
        "location": "Singapore",
        "status": "active",
    },
    {
        "name": "Hassan Ali",
        "role": "Design",
        "level": "Senior",
        "location": "Dubai",
        "status": "away",
    },
    {
        "name": "Iris Tanaka",
        "role": "Product",
        "level": "Lead",
        "location": "Tokyo",
        "status": "active",
    },
    {
        "name": "James Wright",
        "role": "Engineering",
        "level": "Senior",
        "location": "London",
        "status": "inactive",
    },
    {
        "name": "Karen Petrov",
        "role": "Data Science",
        "level": "Lead",
        "location": "Berlin",
        "status": "active",
    },
    {
        "name": "Liam O'Brien",
        "role": "Engineering",
        "level": "Mid",
        "location": "Dublin",
        "status": "active",
    },
]


@mcp.tool(app=True)
def list_team(department: str | None = None) -> PrefabApp:
    """Browse the team directory with sorting and search.

    Args:
        department: Filter by department (e.g. "Engineering", "Design").
                    Leave empty to show everyone.
    """
    if department:
        rows = [e for e in EMPLOYEES if e["role"].lower() == department.lower()]
    else:
        rows = EMPLOYEES

    active = sum(1 for e in rows if e["status"] == "active")

    with Column(gap=6, css_class="p-6") as view:
        with Column(gap=1):
            Heading("Team Directory")
            with Row(gap=2):
                Muted(f"{len(rows)} members")
                Muted(f"{active} active", css_class="text-success")
                if department:
                    Badge(department, variant="outline")

        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name", sortable=True),
                DataTableColumn(key="role", header="Department", sortable=True),
                DataTableColumn(key="level", header="Level", sortable=True),
                DataTableColumn(key="location", header="Location", sortable=True),
                DataTableColumn(key="status", header="Status", sortable=True),
            ],
            rows=rows,
            searchable=True,
            paginated=True,
            page_size=10,
        )

    return PrefabApp(
        title="Team Directory",
        view=view,
        state={"total": len(rows), "active": active},
    )


if __name__ == "__main__":
    mcp.run()
