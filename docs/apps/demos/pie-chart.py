from prefab_ui.app import PrefabApp
from prefab_ui.components import Column
from prefab_ui.components.charts import PieChart

data = [
    {"category": "Bug", "count": 42},
    {"category": "Feature", "count": 28},
    {"category": "Docs", "count": 15},
    {"category": "Infra", "count": 10},
]

with PrefabApp() as app:
    with Column(css_class="p-6"):
        PieChart(
            data=data,
            data_key="count",
            name_key="category",
            inner_radius=50,
            show_legend=True,
            height=240,
        )
