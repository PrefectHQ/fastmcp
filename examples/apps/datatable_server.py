from collections import Counter

from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Card,
    CardContent,
    Column,
    Grid,
    Heading,
    Row,
    Separator,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries, PieChart
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
def team_directory(department: str | None = None) -> PrefabApp:
    """Browse the team directory — sortable, searchable, with department breakdown."""
    rows = [p for p in TEAM if not department or p["role"] == department]

    dept_counts = Counter(p["role"] for p in rows)
    chart_data = [{"department": k, "count": v} for k, v in dept_counts.items()]

    level_counts = Counter(p["level"] for p in rows)
    level_data = [{"level": k, "count": v} for k, v in level_counts.items()]

    with Column(gap=6, css_class="p-6") as view:
        with Row(gap=2, align="center"):
            Heading("Team Directory")
            Badge(f"{len(rows)} people", variant="secondary")

        with Grid(columns=2, gap=6):
            with Card():
                with CardContent():
                    Text(
                        "By Department",
                        css_class="text-sm font-medium text-muted-foreground mb-2",
                    )
                    PieChart(
                        data=chart_data,
                        data_key="count",
                        name_key="department",
                        show_legend=True,
                        inner_radius=40,
                        height=200,
                    )

            with Card():
                with CardContent():
                    Text(
                        "By Level",
                        css_class="text-sm font-medium text-muted-foreground mb-2",
                    )
                    BarChart(
                        data=level_data,
                        series=[ChartSeries(data_key="count", label="People")],
                        x_axis="level",
                        height=200,
                        horizontal=True,
                    )

        Separator()

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

    return PrefabApp(view=view)


if __name__ == "__main__":
    mcp.run()
