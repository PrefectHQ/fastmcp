from prefab_ui.app import PrefabApp
from prefab_ui.components import Column
from prefab_ui.components.charts import BarChart, ChartSeries

data = [
    {"quarter": "Q1", "revenue": 42000, "costs": 28000},
    {"quarter": "Q2", "revenue": 51000, "costs": 31000},
    {"quarter": "Q3", "revenue": 47000, "costs": 29000},
    {"quarter": "Q4", "revenue": 63000, "costs": 35000},
]

with PrefabApp() as app:
    with Column(css_class="p-6"):
        BarChart(
            data=data,
            series=[
                ChartSeries(data_key="revenue", label="Revenue"),
                ChartSeries(data_key="costs", label="Costs"),
            ],
            x_axis="quarter",
            show_legend=True,
            height=250,
        )
