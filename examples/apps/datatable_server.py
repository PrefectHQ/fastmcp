from prefab_ui.components import Column, Heading, Muted
from prefab_ui.components.data_table import DataTable, DataTableColumn

from fastmcp import FastMCP

mcp = FastMCP("Team Directory")

TEAM = [
    {
        "name": "Alice Chen",
        "role": "Engineering",
        "level": "Senior",
        "location": "San Francisco",
    },
    {"name": "Bob Martinez", "role": "Design", "level": "Lead", "location": "New York"},
    {
        "name": "Carol Johnson",
        "role": "Engineering",
        "level": "Staff",
        "location": "London",
    },
    {
        "name": "David Kim",
        "role": "Product",
        "level": "Senior",
        "location": "San Francisco",
    },
    {"name": "Eva Müller", "role": "Engineering", "level": "Mid", "location": "Berlin"},
    {
        "name": "Frank Okafor",
        "role": "Data Science",
        "level": "Senior",
        "location": "Lagos",
    },
    {
        "name": "Grace Liu",
        "role": "Engineering",
        "level": "Junior",
        "location": "Singapore",
    },
    {"name": "Hassan Ali", "role": "Design", "level": "Senior", "location": "Dubai"},
]


@mcp.tool(app=True)
def team_directory(department: str | None = None) -> Column:
    """Browse the team directory — sortable, searchable, paginated."""
    rows = [p for p in TEAM if not department or p["role"] == department]
    with Column(gap=4, css_class="p-6") as view:
        Heading("Team Directory")
        Muted(f"{len(rows)} people")
        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name", sortable=True),
                DataTableColumn(key="role", header="Department", sortable=True),
                DataTableColumn(key="level", header="Level", sortable=True),
                DataTableColumn(key="location", header="Location", sortable=True),
            ],
            rows=rows,
            search=True,
            paginated=True,
        )
    return view


if __name__ == "__main__":
    mcp.run()
