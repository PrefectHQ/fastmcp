from prefab_ui.components import Column, Heading, Muted
from prefab_ui.components.charts import BarChart, ChartSeries

from fastmcp import FastMCP

mcp = FastMCP("Sales Dashboard")

DATA = [
    {"month": "Jan", "online": 4200, "retail": 2400},
    {"month": "Feb", "online": 3800, "retail": 2100},
    {"month": "Mar", "online": 5100, "retail": 2800},
    {"month": "Apr", "online": 4600, "retail": 3200},
    {"month": "May", "online": 5800, "retail": 3100},
    {"month": "Jun", "online": 6200, "retail": 3500},
]


@mcp.tool(app=True)
def sales_chart(stacked: bool = False) -> Column:
    """Show monthly online vs. retail sales as a bar chart."""
    with Column(gap=4, css_class="p-6") as view:
        Heading("Monthly Sales")
        Muted("Online vs. retail — hover bars for details")
        BarChart(
            data=DATA,
            series=[
                ChartSeries(data_key="online", label="Online"),
                ChartSeries(data_key="retail", label="Retail"),
            ],
            x_axis="month",
            stacked=stacked,
            show_legend=True,
        )
    return view


if __name__ == "__main__":
    mcp.run()
