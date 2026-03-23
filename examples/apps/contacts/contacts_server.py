"""Contact manager — a FastMCPApp example with forms and callable tool references.

Demonstrates the full FastMCPApp stack:
- @app.ui() entry point that the model calls to open the app
- @app.tool() backend tools that the UI calls via CallTool
- CallTool(fn) with function references (not strings) that resolve to global keys
- Form.from_model() for auto-generated Pydantic model forms
- Manual form construction with the context-manager pattern

Usage:
    uv run python contacts_server.py               # HTTP (default)
    uv run python contacts_server.py --stdio        # stdio for MCP clients
"""

from __future__ import annotations

from typing import Literal

from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp, set_initial_state
from prefab_ui.components import (
    Badge,
    Button,
    Column,
    ForEach,
    Form,
    Heading,
    Input,
    Muted,
    Row,
    Separator,
    Text,
)
from prefab_ui.rx import ERROR, RESULT, STATE
from pydantic import BaseModel, Field

from fastmcp import FastMCP, FastMCPApp

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

_contacts: list[dict] = [
    {
        "name": "Arthur Dent",
        "email": "arthur@earth.com",
        "category": "Customer",
        "notes": "",
    },
    {
        "name": "Ford Prefect",
        "email": "ford@betelgeuse.org",
        "category": "Partner",
        "notes": "Researcher",
    },
]


# ---------------------------------------------------------------------------
# Pydantic model for auto-generated forms
# ---------------------------------------------------------------------------


class ContactModel(BaseModel):
    name: str = Field(title="Full Name", min_length=1)
    email: str = Field(title="Email")
    category: Literal["Customer", "Vendor", "Partner", "Other"] = "Other"
    notes: str = Field(
        default="",
        title="Notes",
        json_schema_extra={"ui": {"type": "textarea"}},
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastMCPApp("Contacts")


@app.tool()
def save_contact(data: ContactModel) -> list[dict]:
    """Save a new contact and return the updated list."""
    _contacts.append(data.model_dump())
    return list(_contacts)


@app.tool()
def search_contacts(query: str) -> list[dict]:
    """Filter contacts by name or email."""
    q = query.lower()
    return [c for c in _contacts if q in c["name"].lower() or q in c["email"].lower()]


@app.tool(model=True)
def list_contacts() -> list[dict]:
    """Return all contacts. Visible to both the model and the UI."""
    return list(_contacts)


@app.ui()
def contact_manager() -> PrefabApp:
    """Open the contact manager. The model calls this to launch the app."""
    set_initial_state(contacts=list(_contacts))

    with Column(gap=6, css_class="p-6") as view:
        Heading("Contacts")

        with ForEach("contacts") as contact:
            with Row(gap=2, align="center"):
                Text(contact.name, css_class="font-medium")
                Muted(contact.email)
                Badge(contact.category)

        Separator()

        Heading("Add Contact", level=3)
        Form.from_model(
            ContactModel,
            on_submit=CallTool(
                save_contact,
                on_success=[
                    SetState("contacts", RESULT),
                    ShowToast("Contact saved!", variant="success"),
                ],
                on_error=ShowToast(ERROR, variant="error"),
            ),
        )

        Separator()

        Heading("Search", level=3)
        with Form(
            on_submit=CallTool(
                search_contacts,
                arguments={"query": STATE.query},
                on_success=SetState("contacts", RESULT),
            )
        ):
            Input(name="query", placeholder="Search by name or email...")
            Button("Search")

    return PrefabApp(view=view)


mcp = FastMCP("Contacts Server", providers=[app])

if __name__ == "__main__":
    mcp.run(transport="http")
