from prefab_ui.actions import AppendState, SetState, ShowToast
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    H3,
    Button,
    Column,
    DataTable,
    DataTableColumn,
    Form,
    Input,
    Row,
    Select,
    SelectOption,
    Separator,
)
from prefab_ui.rx import STATE

contacts = [
    {"name": "Arthur Dent", "email": "arthur@earth.com", "category": "Customer"},
    {"name": "Ford Prefect", "email": "ford@betelgeuse.org", "category": "Partner"},
    {
        "name": "Trillian Astra",
        "email": "trillian@heartofgold.com",
        "category": "Customer",
    },
    {"name": "Zaphod Beeblebrox", "email": "zaphod@galaxy.gov", "category": "Vendor"},
]

with PrefabApp(
    state={"contacts": contacts, "name": "", "email": "", "category": "Customer"}
) as app:
    with Column(gap=4, css_class="p-6"):
        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name", sortable=True),
                DataTableColumn(key="email", header="Email"),
                DataTableColumn(key="category", header="Category"),
            ],
            rows=STATE.contacts,
            search=True,
        )

        Separator()

        H3("Add Contact")
        with Form(
            on_submit=[
                AppendState(
                    "contacts",
                    {
                        "name": STATE.name,
                        "email": STATE.email,
                        "category": STATE.category,
                    },
                ),
                SetState("name", ""),
                SetState("email", ""),
                ShowToast("Contact saved!", variant="success"),
            ]
        ):
            with Row(gap=4):
                Input(name="name", label="Name", placeholder="Full name", required=True)
                Input(
                    name="email",
                    label="Email",
                    placeholder="name@example.com",
                    required=True,
                )
            with Select(name="category", label="Category"):
                SelectOption(value="Customer", label="Customer")
                SelectOption(value="Partner", label="Partner")
                SelectOption(value="Vendor", label="Vendor")
            Button("Save Contact")
