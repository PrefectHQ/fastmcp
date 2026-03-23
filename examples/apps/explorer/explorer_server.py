"""Data explorer — a FastMCPApp example with tables, charts, and filtering.

Demonstrates the full FastMCPApp stack:
- @app.ui() entry point with a tabbed data exploration interface
- @app.tool() backend tools for analysis, summaries, and filtering
- DataTable with sorting, search, and pagination
- BarChart and PieChart for data visualization
- Metric cards for summary statistics
- Select-driven filtering with CallTool
- State management with set_initial_state() and Rx()

Usage:
    uv run python explorer_server.py               # HTTP (default)
    uv run python explorer_server.py --stdio        # stdio for MCP clients
"""

from __future__ import annotations

from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp, set_initial_state
from prefab_ui.components import (
    Badge,
    Button,
    Card,
    CardContent,
    Column,
    DataTable,
    DataTableColumn,
    Grid,
    Heading,
    Metric,
    Muted,
    Row,
    Select,
    SelectOption,
    Separator,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries, PieChart
from prefab_ui.rx import ERROR, RESULT, STATE

from fastmcp import FastMCP, FastMCPApp

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SALES_DATA: list[dict] = [
    {
        "date": "2025-01-05",
        "product": "Widget A",
        "region": "North",
        "amount": 1200,
        "quantity": 10,
    },
    {
        "date": "2025-01-12",
        "product": "Widget B",
        "region": "South",
        "amount": 850,
        "quantity": 7,
    },
    {
        "date": "2025-01-18",
        "product": "Gadget X",
        "region": "East",
        "amount": 2300,
        "quantity": 15,
    },
    {
        "date": "2025-01-25",
        "product": "Gadget Y",
        "region": "West",
        "amount": 1750,
        "quantity": 12,
    },
    {
        "date": "2025-02-02",
        "product": "Widget A",
        "region": "East",
        "amount": 1400,
        "quantity": 11,
    },
    {
        "date": "2025-02-09",
        "product": "Widget B",
        "region": "North",
        "amount": 920,
        "quantity": 8,
    },
    {
        "date": "2025-02-15",
        "product": "Gadget X",
        "region": "South",
        "amount": 2100,
        "quantity": 14,
    },
    {
        "date": "2025-02-22",
        "product": "Gadget Y",
        "region": "West",
        "amount": 1600,
        "quantity": 11,
    },
    {
        "date": "2025-03-01",
        "product": "Widget A",
        "region": "South",
        "amount": 1350,
        "quantity": 10,
    },
    {
        "date": "2025-03-08",
        "product": "Widget B",
        "region": "West",
        "amount": 780,
        "quantity": 6,
    },
    {
        "date": "2025-03-14",
        "product": "Gadget X",
        "region": "North",
        "amount": 2500,
        "quantity": 17,
    },
    {
        "date": "2025-03-21",
        "product": "Gadget Y",
        "region": "East",
        "amount": 1900,
        "quantity": 13,
    },
    {
        "date": "2025-04-03",
        "product": "Widget A",
        "region": "West",
        "amount": 1100,
        "quantity": 9,
    },
    {
        "date": "2025-04-10",
        "product": "Widget B",
        "region": "East",
        "amount": 960,
        "quantity": 8,
    },
    {
        "date": "2025-04-17",
        "product": "Gadget X",
        "region": "South",
        "amount": 2400,
        "quantity": 16,
    },
    {
        "date": "2025-04-24",
        "product": "Gadget Y",
        "region": "North",
        "amount": 1850,
        "quantity": 12,
    },
    {
        "date": "2025-05-01",
        "product": "Widget A",
        "region": "North",
        "amount": 1500,
        "quantity": 12,
    },
    {
        "date": "2025-05-08",
        "product": "Widget B",
        "region": "South",
        "amount": 890,
        "quantity": 7,
    },
    {
        "date": "2025-05-15",
        "product": "Gadget X",
        "region": "West",
        "amount": 2200,
        "quantity": 15,
    },
    {
        "date": "2025-05-22",
        "product": "Gadget Y",
        "region": "East",
        "amount": 1700,
        "quantity": 11,
    },
    {
        "date": "2025-06-05",
        "product": "Widget A",
        "region": "East",
        "amount": 1300,
        "quantity": 10,
    },
    {
        "date": "2025-06-12",
        "product": "Widget B",
        "region": "North",
        "amount": 1050,
        "quantity": 9,
    },
    {
        "date": "2025-06-19",
        "product": "Gadget X",
        "region": "North",
        "amount": 2600,
        "quantity": 18,
    },
    {
        "date": "2025-06-26",
        "product": "Gadget Y",
        "region": "South",
        "amount": 1650,
        "quantity": 11,
    },
    {
        "date": "2025-07-03",
        "product": "Widget A",
        "region": "South",
        "amount": 1450,
        "quantity": 11,
    },
    {
        "date": "2025-07-10",
        "product": "Widget B",
        "region": "West",
        "amount": 830,
        "quantity": 7,
    },
    {
        "date": "2025-07-17",
        "product": "Gadget X",
        "region": "East",
        "amount": 2350,
        "quantity": 16,
    },
    {
        "date": "2025-07-24",
        "product": "Gadget Y",
        "region": "West",
        "amount": 1800,
        "quantity": 12,
    },
    {
        "date": "2025-08-01",
        "product": "Widget A",
        "region": "West",
        "amount": 1250,
        "quantity": 10,
    },
    {
        "date": "2025-08-08",
        "product": "Widget B",
        "region": "East",
        "amount": 970,
        "quantity": 8,
    },
    {
        "date": "2025-08-15",
        "product": "Gadget X",
        "region": "South",
        "amount": 2450,
        "quantity": 16,
    },
    {
        "date": "2025-08-22",
        "product": "Gadget Y",
        "region": "North",
        "amount": 1950,
        "quantity": 13,
    },
]

REGIONS = ["All", "North", "South", "East", "West"]
PRODUCTS = ["All", "Widget A", "Widget B", "Gadget X", "Gadget Y"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_rows(
    rows: list[dict],
    region: str = "All",
    product: str = "All",
) -> list[dict]:
    filtered = rows
    if region != "All":
        filtered = [r for r in filtered if r["region"] == region]
    if product != "All":
        filtered = [r for r in filtered if r["product"] == product]
    return filtered


def _compute_summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "count": 0,
            "total_amount": 0,
            "avg_amount": 0,
            "min_amount": 0,
            "max_amount": 0,
            "total_quantity": 0,
        }
    amounts = [r["amount"] for r in rows]
    return {
        "count": len(rows),
        "total_amount": sum(amounts),
        "avg_amount": round(sum(amounts) / len(amounts)),
        "min_amount": min(amounts),
        "max_amount": max(amounts),
        "total_quantity": sum(r["quantity"] for r in rows),
    }


def _aggregate_by(rows: list[dict], key: str) -> list[dict]:
    totals: dict[str, int] = {}
    for row in rows:
        label = row[key]
        totals[label] = totals.get(label, 0) + row["amount"]
    return [{key: label, "amount": total} for label, total in sorted(totals.items())]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastMCPApp("Data Explorer")


@app.tool()
def analyze_data(region: str = "All", product: str = "All") -> dict:
    """Filter and analyze sales data. Returns rows, summary, and chart data."""
    filtered = _filter_rows(SALES_DATA, region, product)
    return {
        "rows": filtered,
        "summary": _compute_summary(filtered),
        "by_region": _aggregate_by(filtered, "region"),
        "by_product": _aggregate_by(filtered, "product"),
    }


@app.tool(model=True)
def get_summary() -> dict:
    """Return summary statistics for the full dataset."""
    return _compute_summary(SALES_DATA)


@app.tool()
def filter_data(region: str = "All", product: str = "All") -> list[dict]:
    """Filter sales data by region and/or product."""
    return _filter_rows(SALES_DATA, region, product)


@app.ui()
def data_explorer() -> PrefabApp:
    """Open the data explorer. Browse, filter, and visualize sales data."""

    initial = analyze_data()
    state = set_initial_state(
        rows=initial["rows"],
        summary=initial["summary"],
        by_region=initial["by_region"],
        by_product=initial["by_product"],
        selected_region="All",
        selected_product="All",
        loading=False,
    )

    with Column(gap=6, css_class="p-6") as view:
        Heading("Sales Data Explorer")
        Muted(f"{len(SALES_DATA)} records loaded")

        Separator()

        # ----- Filters -----
        with Row(gap=4, align="center"):
            Text("Filters", css_class="font-semibold")

            with Select(
                name="selected_region",
                placeholder="Region",
                value="All",
                on_change=[
                    SetState("loading", True),
                    CallTool(
                        analyze_data,
                        arguments={
                            "region": STATE.selected_region,
                            "product": STATE.selected_product,
                        },
                        on_success=[
                            SetState("rows", RESULT.rows),
                            SetState("summary", RESULT.summary),
                            SetState("by_region", RESULT.by_region),
                            SetState("by_product", RESULT.by_product),
                            SetState("loading", False),
                            ShowToast("Data updated", variant="success"),
                        ],
                        on_error=[
                            SetState("loading", False),
                            ShowToast(ERROR, variant="error"),
                        ],
                    ),
                ],
            ):
                for region in REGIONS:
                    SelectOption(value=region, label=region)

            with Select(
                name="selected_product",
                placeholder="Product",
                value="All",
                on_change=[
                    SetState("loading", True),
                    CallTool(
                        analyze_data,
                        arguments={
                            "region": STATE.selected_region,
                            "product": STATE.selected_product,
                        },
                        on_success=[
                            SetState("rows", RESULT.rows),
                            SetState("summary", RESULT.summary),
                            SetState("by_region", RESULT.by_region),
                            SetState("by_product", RESULT.by_product),
                            SetState("loading", False),
                            ShowToast("Data updated", variant="success"),
                        ],
                        on_error=[
                            SetState("loading", False),
                            ShowToast(ERROR, variant="error"),
                        ],
                    ),
                ],
            ):
                for product in PRODUCTS:
                    SelectOption(value=product, label=product)

            Button(
                state.loading.then("Loading...", "Reset"),
                disabled=state.loading,
                on_click=[
                    SetState("selected_region", "All"),
                    SetState("selected_product", "All"),
                    SetState("loading", True),
                    CallTool(
                        analyze_data,
                        arguments={"region": "All", "product": "All"},
                        on_success=[
                            SetState("rows", RESULT.rows),
                            SetState("summary", RESULT.summary),
                            SetState("by_region", RESULT.by_region),
                            SetState("by_product", RESULT.by_product),
                            SetState("loading", False),
                        ],
                        on_error=[
                            SetState("loading", False),
                            ShowToast(ERROR, variant="error"),
                        ],
                    ),
                ],
            )

        Separator()

        # ----- Tabs -----
        with Tabs():
            # ---- Summary ----
            with Tab("Summary"):
                with Grid(columns=3, gap=4):
                    with Card():
                        with CardContent():
                            Metric(
                                label="Total Revenue",
                                value=state.summary.total_amount,
                            )
                    with Card():
                        with CardContent():
                            Metric(
                                label="Average Sale",
                                value=state.summary.avg_amount,
                            )
                    with Card():
                        with CardContent():
                            Metric(
                                label="Total Quantity",
                                value=state.summary.total_quantity,
                            )

                with Grid(columns=3, gap=4, css_class="mt-4"):
                    with Card():
                        with CardContent():
                            Metric(
                                label="Transactions",
                                value=state.summary.count,
                            )
                    with Card():
                        with CardContent():
                            Metric(
                                label="Min Sale",
                                value=state.summary.min_amount,
                            )
                    with Card():
                        with CardContent():
                            Metric(
                                label="Max Sale",
                                value=state.summary.max_amount,
                            )

                with Row(gap=2, css_class="mt-4"):
                    Badge(f"Region: {STATE.selected_region}")
                    Badge(f"Product: {STATE.selected_product}")

            # ---- Table ----
            with Tab("Table"):
                DataTable(
                    columns=[
                        DataTableColumn(key="date", header="Date", sortable=True),
                        DataTableColumn(key="product", header="Product", sortable=True),
                        DataTableColumn(key="region", header="Region", sortable=True),
                        DataTableColumn(
                            key="amount", header="Amount ($)", sortable=True
                        ),
                        DataTableColumn(key="quantity", header="Qty", sortable=True),
                    ],
                    rows="{{ rows }}",
                    search=True,
                    paginated=True,
                    page_size=10,
                )

            # ---- Charts ----
            with Tab("Charts"):
                with Grid(columns=2, gap=6):
                    with Column(gap=2):
                        Heading("Revenue by Region", level=3)
                        BarChart(
                            data=state.by_region,
                            series=[ChartSeries(data_key="amount", label="Revenue")],
                            x_axis="region",
                            show_legend=True,
                        )

                    with Column(gap=2):
                        Heading("Revenue by Product", level=3)
                        BarChart(
                            data=state.by_product,
                            series=[ChartSeries(data_key="amount", label="Revenue")],
                            x_axis="product",
                            show_legend=True,
                        )

                Separator(css_class="my-4")

                with Grid(columns=2, gap=6):
                    with Column(gap=2):
                        Heading("Region Breakdown", level=3)
                        PieChart(
                            data=state.by_region,
                            data_key="amount",
                            name_key="region",
                            show_legend=True,
                            inner_radius=60,
                        )

                    with Column(gap=2):
                        Heading("Product Breakdown", level=3)
                        PieChart(
                            data=state.by_product,
                            data_key="amount",
                            name_key="product",
                            show_legend=True,
                            inner_radius=60,
                        )

    return PrefabApp(view=view)


mcp = FastMCP("Data Explorer", providers=[app])

if __name__ == "__main__":
    mcp.run(transport="http")
