from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Column,
    DataTable,
    DataTableColumn,
    Row,
    Separator,
)
from prefab_ui.components.charts import BarChart, ChartSeries
from prefab_ui.components.metric import Metric

monthly = [
    {"month": "Jan", "revenue": 48200, "costs": 31000},
    {"month": "Feb", "revenue": 52100, "costs": 32500},
    {"month": "Mar", "revenue": 61800, "costs": 34200},
    {"month": "Apr", "revenue": 58400, "costs": 33800},
]

deals = [
    {"account": "Acme Corp", "value": "$84,000", "stage": "Won"},
    {"account": "Globex Inc", "value": "$52,000", "stage": "Negotiation"},
    {"account": "Initech", "value": "$31,500", "stage": "Proposal"},
    {"account": "Wayne Enterprises", "value": "$45,000", "stage": "Lost"},
]

rows = [
    {
        "account": d["account"],
        "value": d["value"],
        "stage": Badge(
            d["stage"],
            variant="success"
            if d["stage"] == "Won"
            else "destructive"
            if d["stage"] == "Lost"
            else "secondary",
        ),
    }
    for d in deals
]

total = sum(m["revenue"] for m in monthly)

with PrefabApp() as app:
    with Column(gap=4, css_class="p-6"):
        with Row(gap=6):
            Metric(label="Revenue (Q1-Q4)", value=f"${total:,}")
            Metric(label="Deals", value=f"{len(deals)}")
        BarChart(
            data=monthly,
            series=[
                ChartSeries(data_key="revenue", label="Revenue"),
                ChartSeries(data_key="costs", label="Costs"),
            ],
            x_axis="month",
            show_legend=True,
            height=200,
        )
        Separator()
        DataTable(
            columns=[
                DataTableColumn(key="account", header="Account", sortable=True),
                DataTableColumn(key="value", header="Value", sortable=True),
                DataTableColumn(key="stage", header="Stage"),
            ],
            rows=rows,
        )
