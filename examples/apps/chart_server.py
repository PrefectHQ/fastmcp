"""Chart MCP App — interactive data visualizations with Prefab.

Demonstrates `fastmcp[apps]` with Prefab chart components:
- `BarChart` and `LineChart` for categorical and trend data
- Multiple series, stacking, and curve styles
- Layout composition with `Column`, `Heading`, and `Muted`
- Custom text fallback via `ToolResult`

Usage:
    uv run python chart_server.py              # HTTP (port 8000)
    uv run python chart_server.py --stdio       # stdio for MCP clients
"""

from __future__ import annotations

from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    BarChart,
    ChartSeries,
    Column,
    Heading,
    LineChart,
    Muted,
)

from fastmcp import FastMCP

mcp = FastMCP("Sales Dashboard")

MONTHLY_SALES = [
    {"month": "Jan", "online": 4200, "retail": 2400},
    {"month": "Feb", "online": 3800, "retail": 2100},
    {"month": "Mar", "online": 5100, "retail": 2800},
    {"month": "Apr", "online": 4600, "retail": 3200},
    {"month": "May", "online": 5800, "retail": 3100},
    {"month": "Jun", "online": 6200, "retail": 3500},
]


@mcp.tool(app=True)
def sales_overview(stacked: bool = False) -> PrefabApp:
    """View monthly sales broken down by channel.

    Args:
        stacked: Stack bars to show total revenue per month.
    """
    total = sum(row["online"] + row["retail"] for row in MONTHLY_SALES)

    with Column(gap=6, css_class="p-6") as view:
        with Column(gap=1):
            Heading("Monthly Sales")
            Muted(f"${total:,} total revenue")

        BarChart(
            data=MONTHLY_SALES,
            series=[
                ChartSeries(data_key="online", label="Online"),
                ChartSeries(data_key="retail", label="Retail"),
            ],
            x_axis="month",
            stacked=stacked,
            show_legend=True,
        )

    return PrefabApp(
        title="Sales Dashboard",
        view=view,
    )


@mcp.tool(app=True)
def sales_trend(curve: str = "linear") -> PrefabApp:
    """View sales trends over time as a line chart.

    Args:
        curve: Line style — "linear", "smooth", or "step".
    """
    with Column(gap=6, css_class="p-6") as view:
        with Column(gap=1):
            Heading("Sales Trend")
            Muted("Online vs. retail over 6 months")

        LineChart(
            data=MONTHLY_SALES,
            series=[
                ChartSeries(data_key="online", label="Online"),
                ChartSeries(data_key="retail", label="Retail"),
            ],
            x_axis="month",
            curve=curve,
            show_dots=True,
            show_legend=True,
        )

    return PrefabApp(
        title="Sales Trend",
        view=view,
    )


if __name__ == "__main__":
    mcp.run()
