from prefab_ui.components import (
    Card,
    CardContent,
    Column,
    Grid,
    Heading,
    Metric,
    Muted,
    Row,
    Separator,
    Text,
)
from prefab_ui.components.charts import AreaChart, ChartSeries, PieChart
from prefab_ui.components.data_table import DataTable, DataTableColumn

from fastmcp import FastMCP

mcp = FastMCP("Sales Dashboard")

MONTHLY_REVENUE = [
    {"month": "Jul", "new_business": 182_000, "expansion": 74_000, "renewal": 210_000},
    {"month": "Aug", "new_business": 195_000, "expansion": 81_000, "renewal": 215_000},
    {"month": "Sep", "new_business": 224_000, "expansion": 93_000, "renewal": 208_000},
    {"month": "Oct", "new_business": 210_000, "expansion": 88_000, "renewal": 222_000},
    {"month": "Nov", "new_business": 248_000, "expansion": 102_000, "renewal": 230_000},
    {"month": "Dec", "new_business": 271_000, "expansion": 115_000, "renewal": 238_000},
    {"month": "Jan", "new_business": 235_000, "expansion": 97_000, "renewal": 241_000},
    {"month": "Feb", "new_business": 262_000, "expansion": 108_000, "renewal": 245_000},
    {"month": "Mar", "new_business": 289_000, "expansion": 121_000, "renewal": 252_000},
    {"month": "Apr", "new_business": 305_000, "expansion": 134_000, "renewal": 258_000},
    {"month": "May", "new_business": 318_000, "expansion": 142_000, "renewal": 263_000},
    {"month": "Jun", "new_business": 342_000, "expansion": 156_000, "renewal": 270_000},
]

REVENUE_BY_SEGMENT = [
    {"segment": "Enterprise", "revenue": 3_840_000},
    {"segment": "Mid-Market", "revenue": 2_160_000},
    {"segment": "SMB", "revenue": 1_440_000},
    {"segment": "Startup", "revenue": 720_000},
]

RECENT_DEALS = [
    {
        "company": "Meridian Health Systems",
        "amount": "$485,000",
        "stage": "Closed Won",
        "rep": "Sarah Chen",
        "close_date": "Jun 12, 2026",
    },
    {
        "company": "Atlas Financial Group",
        "amount": "$372,000",
        "stage": "Closed Won",
        "rep": "Marcus Rivera",
        "close_date": "Jun 10, 2026",
    },
    {
        "company": "Pinnacle Manufacturing",
        "amount": "$298,000",
        "stage": "Negotiation",
        "rep": "Aisha Patel",
        "close_date": "Jun 28, 2026",
    },
    {
        "company": "Crestview Logistics",
        "amount": "$264,000",
        "stage": "Proposal Sent",
        "rep": "James O'Brien",
        "close_date": "Jul 5, 2026",
    },
    {
        "company": "Northstar Retail",
        "amount": "$215,000",
        "stage": "Closed Won",
        "rep": "Sarah Chen",
        "close_date": "Jun 8, 2026",
    },
    {
        "company": "Ironclad Security",
        "amount": "$189,000",
        "stage": "Negotiation",
        "rep": "Lena Kowalski",
        "close_date": "Jul 1, 2026",
    },
    {
        "company": "Summit Analytics",
        "amount": "$176,000",
        "stage": "Closed Won",
        "rep": "Marcus Rivera",
        "close_date": "Jun 5, 2026",
    },
    {
        "company": "Brightpath Education",
        "amount": "$142,000",
        "stage": "Proposal Sent",
        "rep": "Aisha Patel",
        "close_date": "Jul 12, 2026",
    },
    {
        "company": "Vantage Media",
        "amount": "$128,000",
        "stage": "Closed Won",
        "rep": "Lena Kowalski",
        "close_date": "Jun 3, 2026",
    },
    {
        "company": "Redwood Hospitality",
        "amount": "$97,000",
        "stage": "Discovery",
        "rep": "James O'Brien",
        "close_date": "Jul 20, 2026",
    },
]


@mcp.tool(app=True)
def sales_dashboard() -> Column:
    """Company sales dashboard with KPIs, revenue trends, segment breakdown, and recent deals."""
    total_revenue = sum(
        row["new_business"] + row["expansion"] + row["renewal"]
        for row in MONTHLY_REVENUE
    )
    current_quarter = sum(
        row["new_business"] + row["expansion"] + row["renewal"]
        for row in MONTHLY_REVENUE[-3:]
    )
    prior_quarter = sum(
        row["new_business"] + row["expansion"] + row["renewal"]
        for row in MONTHLY_REVENUE[-6:-3]
    )
    growth_pct = (current_quarter - prior_quarter) / prior_quarter * 100

    with Column(gap=6, css_class="p-6") as view:
        with Row(gap=2, align="center"):
            Heading("Sales Dashboard")
            Muted("FY2026  |  Last updated Jun 15, 2026")

        with Grid(columns=4, gap=4):
            with Card():
                with CardContent():
                    Metric(
                        label="Total Revenue",
                        value=f"${total_revenue / 1_000_000:.1f}M",
                        delta="+18.2% YoY",
                        trend="up",
                    )

            with Card():
                with CardContent():
                    Metric(
                        label="Quarterly Growth",
                        value=f"{growth_pct:.1f}%",
                        delta="+3.8pp vs prior",
                        trend="up",
                    )

            with Card():
                with CardContent():
                    Metric(
                        label="Active Customers",
                        value="1,847",
                        delta="+124 this quarter",
                        trend="up",
                    )

            with Card():
                with CardContent():
                    Metric(
                        label="Avg Deal Size",
                        value="$236K",
                        delta="+12% vs H1",
                        trend="up",
                    )

        with Grid(columns=3, gap=6):
            with Card(css_class="col-span-2"):
                with CardContent():
                    Text(
                        "Monthly Revenue",
                        css_class="text-sm font-medium text-muted-foreground mb-2",
                    )
                    AreaChart(
                        data=MONTHLY_REVENUE,
                        series=[
                            ChartSeries(data_key="new_business", label="New Business"),
                            ChartSeries(data_key="expansion", label="Expansion"),
                            ChartSeries(data_key="renewal", label="Renewal"),
                        ],
                        x_axis="month",
                        stacked=True,
                        curve="smooth",
                        show_legend=True,
                        height=280,
                        y_axis_format="compact",
                    )

            with Card():
                with CardContent():
                    Text(
                        "Revenue by Segment",
                        css_class="text-sm font-medium text-muted-foreground mb-2",
                    )
                    PieChart(
                        data=REVENUE_BY_SEGMENT,
                        data_key="revenue",
                        name_key="segment",
                        show_legend=True,
                        inner_radius=50,
                        height=280,
                    )

        Separator()

        Text("Recent Deals", css_class="text-lg font-semibold")

        DataTable(
            columns=[
                DataTableColumn(key="company", header="Company", sortable=True),
                DataTableColumn(key="amount", header="Amount", sortable=True),
                DataTableColumn(key="stage", header="Stage", sortable=True),
                DataTableColumn(key="rep", header="Sales Rep", sortable=True),
                DataTableColumn(key="close_date", header="Close Date", sortable=True),
            ],
            rows=RECENT_DEALS,
            search=True,
            paginated=True,
        )

    return view


if __name__ == "__main__":
    mcp.run()
