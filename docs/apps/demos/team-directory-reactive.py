from collections import Counter

from prefab_ui.actions import SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    H3,
    Badge,
    Card,
    CardContent,
    CardHeader,
    Column,
    DataTable,
    DataTableColumn,
    Grid,
    Row,
    Small,
    Text,
)
from prefab_ui.components.charts import PieChart
from prefab_ui.components.control_flow import If
from prefab_ui.rx import STATE, Rx

MEMBERS = [
    {
        "name": "Alice Chen",
        "role": "Staff Engineer",
        "office": "San Francisco",
        "email": "alice@company.com",
        "projects": 3,
    },
    {
        "name": "Bob Martinez",
        "role": "Lead Designer",
        "office": "New York",
        "email": "bob@company.com",
        "projects": 5,
    },
    {
        "name": "Carol Johnson",
        "role": "Senior Engineer",
        "office": "London",
        "email": "carol@company.com",
        "projects": 2,
    },
    {
        "name": "David Kim",
        "role": "Product Manager",
        "office": "San Francisco",
        "email": "david@company.com",
        "projects": 7,
    },
    {
        "name": "Eva Mueller",
        "role": "Engineer",
        "office": "Berlin",
        "email": "eva@company.com",
        "projects": 1,
    },
    {
        "name": "Frank Lee",
        "role": "Data Scientist",
        "office": "San Francisco",
        "email": "frank@company.com",
        "projects": 4,
    },
    {
        "name": "Grace Park",
        "role": "Engineering Manager",
        "office": "New York",
        "email": "grace@company.com",
        "projects": 6,
    },
]

OFFICE_COUNTS = [
    {"office": office, "count": count}
    for office, count in Counter(m["office"] for m in MEMBERS).items()
]

with PrefabApp(state={"selected": None}) as app:
    with Column(gap=4, css_class="p-6"):
        with Grid(columns=[1, 2], gap=4):
            PieChart(
                data=OFFICE_COUNTS,
                data_key="count",
                name_key="office",
                show_legend=True,
            )
            DataTable(
                columns=[
                    DataTableColumn(key="name", header="Name", sortable=True),
                    DataTableColumn(key="role", header="Role", sortable=True),
                    DataTableColumn(key="office", header="Office", sortable=True),
                ],
                rows=MEMBERS,
                search=True,
                on_row_click=SetState("selected", Rx("$event")),
            )

        with If(STATE.selected):
            with Card():
                with CardHeader():
                    with Row(gap=2, align="center"):
                        H3(Rx("selected.name"))
                        Badge(Rx("selected.office"))
                with CardContent():
                    with Grid(columns=3, gap=4):
                        with Column(gap=0):
                            Small("Role")
                            Text(Rx("selected.role"))
                        with Column(gap=0):
                            Small("Email")
                            Text(Rx("selected.email"))
                        with Column(gap=0):
                            Small("Active Projects")
                            Text(Rx("selected.projects"))
