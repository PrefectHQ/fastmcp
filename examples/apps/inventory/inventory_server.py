"""Inventory tracker -- a FastMCPApp example with CRUD operations and rich UI.

Demonstrates the full FastMCPApp stack:
- @app.ui() entry point that the model calls to open the app
- @app.tool() backend tools for add, update, delete, and search
- DataTable with sortable columns and built-in search
- Form.from_model() for auto-generated Pydantic model forms
- Tabs, Select filtering, ForEach results, and Toast notifications
- State management with set_initial_state() and Rx()

Usage:
    uv run python inventory_server.py               # HTTP (default)
    uv run python inventory_server.py --stdio        # stdio for MCP clients
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    ForEach,
    Form,
    Grid,
    Heading,
    Input,
    Muted,
    Row,
    Select,
    SelectOption,
    Separator,
    Tab,
    Tabs,
    Text,
)
from prefab_ui.rx import RESULT, Rx
from pydantic import BaseModel, Field

from fastmcp import FastMCP, FastMCPApp

# ---------------------------------------------------------------------------
# Data store
# ---------------------------------------------------------------------------

_next_id = 11

_inventory: list[dict] = [
    {
        "id": 1,
        "name": "Wireless Mouse",
        "category": "Electronics",
        "quantity": 45,
        "price": 29.99,
        "last_updated": "2026-03-20",
    },
    {
        "id": 2,
        "name": "Mechanical Keyboard",
        "category": "Electronics",
        "quantity": 32,
        "price": 89.99,
        "last_updated": "2026-03-19",
    },
    {
        "id": 3,
        "name": "USB-C Hub",
        "category": "Electronics",
        "quantity": 18,
        "price": 49.99,
        "last_updated": "2026-03-18",
    },
    {
        "id": 4,
        "name": "A4 Copy Paper (500 sheets)",
        "category": "Office Supplies",
        "quantity": 200,
        "price": 8.50,
        "last_updated": "2026-03-21",
    },
    {
        "id": 5,
        "name": "Ballpoint Pens (box)",
        "category": "Office Supplies",
        "quantity": 150,
        "price": 12.00,
        "last_updated": "2026-03-20",
    },
    {
        "id": 6,
        "name": "Sticky Notes (pack)",
        "category": "Office Supplies",
        "quantity": 85,
        "price": 5.99,
        "last_updated": "2026-03-17",
    },
    {
        "id": 7,
        "name": "Standing Desk",
        "category": "Furniture",
        "quantity": 8,
        "price": 499.00,
        "last_updated": "2026-03-15",
    },
    {
        "id": 8,
        "name": "Ergonomic Chair",
        "category": "Furniture",
        "quantity": 12,
        "price": 349.00,
        "last_updated": "2026-03-16",
    },
    {
        "id": 9,
        "name": "Monitor Arm",
        "category": "Furniture",
        "quantity": 25,
        "price": 79.99,
        "last_updated": "2026-03-22",
    },
    {
        "id": 10,
        "name": "Webcam HD",
        "category": "Electronics",
        "quantity": 60,
        "price": 69.99,
        "last_updated": "2026-03-21",
    },
]

CATEGORIES = ["All", "Electronics", "Office Supplies", "Furniture"]


# ---------------------------------------------------------------------------
# Pydantic model for add-item form
# ---------------------------------------------------------------------------


class NewItem(BaseModel):
    name: str = Field(title="Item Name", min_length=1)
    category: Literal["Electronics", "Office Supplies", "Furniture"] = Field(
        title="Category",
        default="Electronics",
    )
    quantity: int = Field(title="Quantity", ge=0, default=1)
    price: float = Field(title="Unit Price ($)", ge=0.0, default=0.0)


# ---------------------------------------------------------------------------
# App and tools
# ---------------------------------------------------------------------------

app = FastMCPApp("Inventory")


@app.tool()
def add_item(data: NewItem) -> list[dict]:
    """Add a new item to inventory and return the full list."""
    global _next_id
    item = {
        "id": _next_id,
        "name": data.name,
        "category": data.category,
        "quantity": data.quantity,
        "price": data.price,
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }
    _next_id += 1
    _inventory.append(item)
    return list(_inventory)


@app.tool()
def update_quantity(item_id: int, delta: int) -> list[dict]:
    """Adjust an item's quantity by delta (+/-) and return the full list."""
    for item in _inventory:
        if item["id"] == item_id:
            new_qty = max(0, item["quantity"] + delta)
            item["quantity"] = new_qty
            item["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            break
    return list(_inventory)


@app.tool()
def delete_item(item_id: int) -> list[dict]:
    """Remove an item by ID and return the remaining inventory."""
    for i, item in enumerate(_inventory):
        if item["id"] == item_id:
            _inventory.pop(i)
            break
    return list(_inventory)


@app.tool()
def search_items(query: str) -> list[dict]:
    """Search items by name (case-insensitive). Returns matching items."""
    q = query.lower()
    return [item for item in _inventory if q in item["name"].lower()]


@app.tool()
def filter_by_category(category: str) -> list[dict]:
    """Filter inventory by category. Pass 'All' to show everything."""
    if category == "All":
        return list(_inventory)
    return [item for item in _inventory if item["category"] == category]


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _build_inventory_table() -> None:
    """Render the main DataTable with all current items."""
    DataTable(
        columns=[
            DataTableColumn(key="name", header="Name", sortable=True),
            DataTableColumn(key="category", header="Category", sortable=True),
            DataTableColumn(key="quantity", header="Qty", sortable=True),
            DataTableColumn(key="price", header="Price ($)", sortable=True),
            DataTableColumn(key="last_updated", header="Updated", sortable=True),
        ],
        rows=list(_inventory),
        searchable=True,
        paginated=True,
        page_size=10,
    )


def _build_search_section() -> None:
    """Render the search form with ForEach results."""
    Heading("Search Items", level=3)
    Muted("Search by name across all inventory items.")

    with Form(
        on_submit=CallTool(
            search_items,
            arguments={"query": "{{ query }}"},
            on_success=SetState("search_results", RESULT),
        )
    ):
        Input(name="query", placeholder="Search by name...")
        Button("Search")

    with ForEach("search_results") as result:
        with Card(css_class="mb-2"):
            with CardContent():
                with Row(gap=3, align="center"):
                    Text(result.name, css_class="font-medium")
                    Badge(result.category)
                    Text(result.quantity)
                    Muted("in stock")


def _build_add_form() -> None:
    """Render the add-item form using Form.from_model()."""
    Heading("Add New Item", level=3)
    Muted("Fill out the form below to add a new item to inventory.")

    Form.from_model(
        NewItem,
        submit_label="Add Item",
        on_submit=CallTool(
            add_item,
            on_success=[
                SetState("recent_additions", RESULT),
                ShowToast("Item added!", variant="success"),
            ],
            on_error=ShowToast("{{ $error }}", variant="error"),
        ),
    )


def _build_actions_section() -> None:
    """Render category filter, quantity adjustment, and delete controls."""

    # Category filter
    Heading("Filter by Category", level=3)
    Muted("Select a category to see matching items.")

    with Form(
        on_submit=CallTool(
            filter_by_category,
            arguments={"category": "{{ selected_category }}"},
            on_success=SetState("filtered_items", RESULT),
        )
    ):
        with Select(name="selected_category", placeholder="Choose a category..."):
            for cat in CATEGORIES:
                SelectOption(cat, value=cat)
        Button("Apply Filter")

    with ForEach("filtered_items") as item:
        with Row(gap=3, align="center", css_class="py-1"):
            Badge(item.id, variant="outline")
            Text(item.name, css_class="font-medium")
            Badge(item.category)
            Muted(item.quantity)

    Separator()

    # Quantity adjustment
    Heading("Adjust Quantity", level=3)
    Muted("Enter an item ID and use the buttons to adjust stock levels.")

    Input(name="adjust_id", input_type="number", placeholder="Item ID (e.g. 1)")

    with Row(gap=2):
        Button(
            "- 1",
            variant="outline",
            on_click=CallTool(
                update_quantity,
                arguments={"item_id": "{{ adjust_id }}", "delta": -1},
                on_success=[
                    SetState("filtered_items", RESULT),
                    ShowToast("Quantity decreased", variant="default"),
                ],
                on_error=ShowToast("{{ $error }}", variant="error"),
            ),
        )
        Button(
            "+ 1",
            variant="outline",
            on_click=CallTool(
                update_quantity,
                arguments={"item_id": "{{ adjust_id }}", "delta": 1},
                on_success=[
                    SetState("filtered_items", RESULT),
                    ShowToast("Quantity increased", variant="default"),
                ],
                on_error=ShowToast("{{ $error }}", variant="error"),
            ),
        )
        Button(
            "+ 10",
            on_click=CallTool(
                update_quantity,
                arguments={"item_id": "{{ adjust_id }}", "delta": 10},
                on_success=[
                    SetState("filtered_items", RESULT),
                    ShowToast("Restocked +10", variant="success"),
                ],
                on_error=ShowToast("{{ $error }}", variant="error"),
            ),
        )

    Separator()

    # Delete
    Heading("Delete Item", level=3)
    Muted("Permanently remove an item by its ID.")

    with Form(
        on_submit=CallTool(
            delete_item,
            arguments={"item_id": "{{ delete_id }}"},
            on_success=[
                SetState("filtered_items", RESULT),
                ShowToast("Item deleted", variant="warning"),
            ],
            on_error=ShowToast("{{ $error }}", variant="error"),
        )
    ):
        Input(name="delete_id", input_type="number", placeholder="Item ID to delete")
        Button("Delete", variant="destructive")


# ---------------------------------------------------------------------------
# Entry point UI
# ---------------------------------------------------------------------------


@app.ui()
def inventory_manager() -> PrefabApp:
    """Open the inventory manager. The model calls this to launch the app."""
    set_initial_state(
        search_results=[],
        filtered_items=list(_inventory),
        recent_additions=[],
        selected_category="All",
        adjust_id="",
        delete_id="",
        query="",
    )

    with Column(gap=6, css_class="p-6") as view:
        with Row(gap=3, align="center"):
            Heading("Inventory Tracker")
            Badge(
                Rx("filtered_items.length"),
                variant="secondary",
            )
            Muted("items tracked")

        Separator()

        # Summary cards per category
        with Grid(columns=3, gap=4):
            for cat in ["Electronics", "Office Supplies", "Furniture"]:
                count = sum(1 for it in _inventory if it["category"] == cat)
                total_qty = sum(
                    it["quantity"] for it in _inventory if it["category"] == cat
                )
                with Card():
                    with CardContent():
                        Text(cat, css_class="font-medium")
                        Muted(f"{count} items, {total_qty} units")

        with Tabs():
            with Tab("All Items"):
                _build_inventory_table()

            with Tab("Search"):
                _build_search_section()

            with Tab("Add Item"):
                _build_add_form()

            with Tab("Actions"):
                _build_actions_section()

    return PrefabApp(view=view)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Inventory Server", providers=[app])

if __name__ == "__main__":
    mcp.run(transport="http")
