from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Column,
    Row,
    Select,
    SelectOption,
    Switch,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries
from prefab_ui.components.control_flow import If
from prefab_ui.components.metric import Metric
from prefab_ui.rx import Rx

region = Rx("region")

north = [
    {"month": "Jan", "sales": 22000},
    {"month": "Feb", "sales": 25500},
    {"month": "Mar", "sales": 24200},
]
south = [
    {"month": "Jan", "sales": 5800},
    {"month": "Feb", "sales": 6400},
    {"month": "Mar", "sales": 5600},
]
west = [
    {"month": "Jan", "sales": 6000},
    {"month": "Feb", "sales": 6000},
    {"month": "Mar", "sales": 5600},
]

with PrefabApp(
    state={
        "region": "north",
        "north": north,
        "south": south,
        "west": west,
        "show_target": True,
    },
) as app:
    with Column(
        gap=4,
        css_class="p-6",
        let={
            "data": "{{ region == 'south' ? south : region == 'west' ? west : north }}",
        },
    ):
        with Row(gap=4, align="center"):
            with Select(name="region", css_class="w-40"):
                SelectOption(value="north", label="North")
                SelectOption(value="south", label="South")
                SelectOption(value="west", label="West")
            Switch(name="show_target", css_class="ml-auto")
            Text("Show target", css_class="text-sm text-muted-foreground")
        BarChart(
            data=Rx("data"),
            series=[ChartSeries(data_key="sales", label="Sales")],
            x_axis="month",
            height=200,
        )
        with If(Rx("show_target")):
            Metric(
                label="Q1 Target",
                value="$75,000",
            )
