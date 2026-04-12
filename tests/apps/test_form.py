"""Tests for the FormInput provider."""

import json

import pydantic
import pytest

from fastmcp import FastMCP
from fastmcp.apps.form import FormInput, _backfill_boolean_defaults
from fastmcp.server.providers.addressing import hashed_backend_name


class Contact(pydantic.BaseModel):
    name: str
    email: str
    phone: str | None = None


class NoteForm(pydantic.BaseModel):
    title: str
    content: str
    archived: bool = False


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
            hashed_backend_name("Contact", "submit_form"),
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
            hashed_backend_name("Contact", "submit_form"),
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

    async def test_submit_boolean_false_omitted(self):
        """Unchecked checkboxes omit the field; submit_form should still succeed."""
        server = FastMCP("test", providers=[FormInput(model=NoteForm)])

        result = await server.call_tool(
            hashed_backend_name("NoteForm", "submit_form"),
            {"data": {"title": "My Note", "content": "Hello"}},
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        parsed = json.loads(text)
        assert parsed["title"] == "My Note"
        assert parsed["archived"] is False

    async def test_submit_boolean_true_preserved(self):
        """When a boolean field is explicitly True, it should be preserved."""
        server = FastMCP("test", providers=[FormInput(model=NoteForm)])

        result = await server.call_tool(
            hashed_backend_name("NoteForm", "submit_form"),
            {"data": {"title": "My Note", "content": "Hello", "archived": True}},
        )
        text = result.content[0].text  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        parsed = json.loads(text)
        assert parsed["archived"] is True

    async def test_submit_no_data_does_not_crash(self):
        """When data is omitted entirely, the tool should not raise a missing argument error."""
        server = FastMCP("test", providers=[FormInput(model=NoteForm)])

        # Should reach model validation (not crash with "missing required argument"
        # for the data parameter itself). Pydantic will still reject missing
        # required fields like title/content, but that's expected.
        with pytest.raises(pydantic.ValidationError, match="title"):
            await server.call_tool(hashed_backend_name("NoteForm", "submit_form"), {})

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


class TestBackfillBooleanDefaults:
    def test_missing_bool_with_default_gets_backfilled(self):
        data = {"title": "Note", "content": "Body"}
        result = _backfill_boolean_defaults(NoteForm, data)
        assert result["archived"] is False

    def test_present_bool_not_overwritten(self):
        data = {"title": "Note", "content": "Body", "archived": True}
        result = _backfill_boolean_defaults(NoteForm, data)
        assert result["archived"] is True

    def test_required_bool_without_default_gets_false(self):
        class FormWithRequiredBool(pydantic.BaseModel):
            name: str
            active: bool

        data = {"name": "Test"}
        result = _backfill_boolean_defaults(FormWithRequiredBool, data)
        assert result["active"] is False

    def test_non_bool_fields_untouched(self):
        data = {"title": "Note"}
        result = _backfill_boolean_defaults(NoteForm, data)
        assert "content" not in result
