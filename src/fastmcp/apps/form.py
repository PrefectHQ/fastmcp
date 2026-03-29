"""FormInput — a Provider that collects structured input from the user.

Define a Pydantic model for the data you need, and ``FormInput``
generates a form UI. The user fills it out, the submission is
validated, and an optional callback processes the result.

Requires ``fastmcp[apps]`` (prefab-ui).

Usage::

    from pydantic import BaseModel
    from fastmcp import FastMCP
    from fastmcp.apps.form import FormInput

    class ShippingAddress(BaseModel):
        street: str
        city: str
        state: str
        zip_code: str

    mcp = FastMCP("My Server")
    mcp.add_provider(FormInput(model=ShippingAddress))
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

try:
    from prefab_ui.actions import SetState
    from prefab_ui.actions.mcp import CallTool, SendMessage
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import (
        H3,
        Card,
        CardContent,
        CardFooter,
        CardHeader,
        Column,
        Form,
        Muted,
    )
    from prefab_ui.components.control_flow import If
    from prefab_ui.rx import RESULT, STATE
except ImportError as _exc:
    raise ImportError(
        "FormInput requires prefab-ui. Install with: pip install 'fastmcp[apps]'"
    ) from _exc

import pydantic

from fastmcp.apps.app import FastMCPApp


class FormInput(FastMCPApp):
    """A Provider that collects structured input via a Pydantic model.

    Define a model for the data you need, and ``FormInput`` generates
    a form from it using ``Form.from_model()``. Field types, labels,
    descriptions, and validation are all derived from the model.

    Optionally provide an ``on_submit`` callback to process the
    validated data. The callback receives a model instance and returns
    a string that goes back to the LLM. Without a callback, the
    validated JSON is sent directly.

    Example::

        from pydantic import BaseModel
        from fastmcp import FastMCP
        from fastmcp.apps.form import FormInput

        class Contact(BaseModel):
            name: str
            email: str

        mcp = FastMCP("My Server")
        mcp.add_provider(FormInput(model=Contact))

    With a callback::

        def save_contact(contact: Contact) -> str:
            db.insert(contact.model_dump())
            return f"Saved {contact.name}"

        mcp.add_provider(FormInput(model=Contact, on_submit=save_contact))
    """

    def __init__(
        self,
        model: type[pydantic.BaseModel],
        *,
        name: str | None = None,
        title: str | None = None,
        submit_text: str = "Submit",
        tool_name: str | None = None,
        on_submit: Callable[..., str] | None = None,
        send_message: bool = False,
    ) -> None:
        app_name = name or model.__name__
        super().__init__(app_name)
        self._model = model
        self._title = title or model.__name__
        self._submit_text = submit_text
        self._tool_name = tool_name or f"collect_{model.__name__.lower()}"
        self._on_submit = on_submit
        self._send_message = send_message
        self._register_tools()

    def __repr__(self) -> str:
        return f"FormInput({self._model.__name__!r})"

    def _register_tools(self) -> None:
        provider = self
        model = self._model

        @self.tool()
        def submit_form(data: dict[str, Any]) -> str:
            """Validate and process form submission."""
            validated = model.model_validate(data)
            if provider._on_submit is not None:
                return provider._on_submit(validated)
            return json.dumps(validated.model_dump(mode="json"))

        @self.ui(
            name=provider._tool_name,
            description=(
                f"Collect {model.__name__} information from the user via a form. "
                f"Call this tool when you need the user to provide "
                f"{model.__name__} data. The user will see a validated form. "
                f"After calling this tool, STOP and wait for the user to submit."
            ),
        )
        def collect_input(
            prompt: str,
            title: str | None = None,
            submit_text: str | None = None,
        ) -> PrefabApp:
            """Collect structured input from the user.

            Args:
                prompt: Tell the user what you need and why.
                title: Optional heading for the form card.
                submit_text: Optional label for the submit button.
            """
            _title = title or provider._title
            _submit = submit_text or provider._submit_text

            with Card(css_class="max-w-lg mx-auto") as view:
                with CardHeader():
                    H3(_title)

                with CardContent(), Column(gap=4):
                    Muted(prompt)

                    on_success_actions: list[Any] = [
                        SetState("submitted", True),
                    ]
                    if provider._send_message:
                        on_success_actions.insert(
                            0,
                            SendMessage(RESULT),  # ty:ignore[invalid-argument-type]
                        )

                    Form.from_model(
                        model,
                        submit_label=_submit,
                        on_submit=[
                            CallTool(
                                "submit_form",
                                on_success=on_success_actions,
                            ),
                        ],
                    )

                with CardFooter(), If(STATE.submitted):
                    Muted("Submitted.")

            return PrefabApp(
                view=view,
                state={"submitted": False},
            )
