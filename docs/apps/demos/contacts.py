from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    H3,
    Badge,
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

rows = [
    {
        "name": c["name"],
        "email": c["email"],
        "category": Badge(
            c["category"],
            variant="success"
            if c["category"] == "Customer"
            else "secondary"
            if c["category"] == "Partner"
            else "outline",
        ),
    }
    for c in contacts
]

with PrefabApp() as app:
    with Column(gap=4, css_class="p-6"):
        DataTable(
            columns=[
                DataTableColumn(key="name", header="Name", sortable=True),
                DataTableColumn(key="email", header="Email"),
                DataTableColumn(key="category", header="Category"),
            ],
            rows=rows,
            search=True,
        )

        Separator()

        H3("Add Contact")
        with Form():
            with Row(gap=4):
                Input(name="name", label="Name", placeholder="Full name")
                Input(name="email", label="Email", placeholder="name@example.com")
            with Select(name="category", label="Category"):
                SelectOption(value="Customer", label="Customer")
                SelectOption(value="Partner", label="Partner")
                SelectOption(value="Vendor", label="Vendor")
            Button("Save Contact")
