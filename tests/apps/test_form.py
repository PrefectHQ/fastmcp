"""Tests for the FormInput provider."""

import json

import pydantic

from fastmcp import FastMCP
from fastmcp.apps.form import FormInput


class Contact(pydantic.BaseModel):
    name: str
    email: str
    phone: str | None = None


class TestFormInputProvider:
    async def test_collect_returns_structured_content(self):
        server = FastMCP("test", providers=[FormInput(model=Contact)])

        result = await server.call_tool(
            "collect_contact",
            {"prompt": "Enter your details"},
        )
        assert result.structured_content is not None

    async def test_tool_name_derived_from_model(self):
        server = FastMCP("test", providers=[FormInput(model=Contact)])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "collect_contact" in tool_names

    async def test_custom_tool_name(self):
        server = FastMCP(
            "test",
            providers=[FormInput(model=Contact, tool_name="new_contact")],
        )

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "new_contact" in tool_names

    async def test_submit_validates_and_returns_json(self):
        server = FastMCP("test", providers=[FormInput(model=Contact)])

        result = await server.call_tool(
            "Contact___submit_form",
            {"data": {"name": "Alice", "email": "alice@example.com"}},
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        parsed = json.loads(text)
        assert parsed["name"] == "Alice"
        assert parsed["email"] == "alice@example.com"
        assert parsed["phone"] is None

    async def test_submit_with_callback(self):
        saved: list[Contact] = []

        def on_submit(contact: Contact) -> str:
            saved.append(contact)
            return f"Saved {contact.name}"

        server = FastMCP(
            "test",
            providers=[FormInput(model=Contact, on_submit=on_submit)],
        )

        result = await server.call_tool(
            "Contact___submit_form",
            {"data": {"name": "Bob", "email": "bob@example.com"}},
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert "Saved Bob" in text
        assert len(saved) == 1
        assert saved[0].name == "Bob"

    async def test_backend_tool_hidden(self):
        server = FastMCP("test", providers=[FormInput(model=Contact)])

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "_submit_form" not in tool_names

    async def test_multiple_models(self):
        class Address(pydantic.BaseModel):
            street: str
            city: str

        server = FastMCP(
            "test",
            providers=[
                FormInput(model=Contact),
                FormInput(model=Address),
            ],
        )

        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "collect_contact" in tool_names
        assert "collect_address" in tool_names
