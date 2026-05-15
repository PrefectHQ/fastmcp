"""Choice — a Provider that lets the user pick from a set of options.

The LLM presents options, the user clicks one, and the selection
flows back into the conversation as a message.

Requires ``fastmcp[apps]`` (prefab-ui).

Usage::

    from fastmcp import FastMCP
    from fastmcp.apps.choice import Choice

    mcp = FastMCP("My Server")
    mcp.add_provider(Choice())
"""

from __future__ import annotations

from typing import Literal

try:
    from prefab_ui.actions import SetState
    from prefab_ui.actions.mcp import SendMessage
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import (
        H3,
        Button,
        Card,
        CardContent,
        CardFooter,
        CardHeader,
        Column,
        Muted,
        Text,
    )
    from prefab_ui.components.control_flow import If
    from prefab_ui.rx import STATE
except ImportError as _exc:
    raise ImportError(
        "Choice requires prefab-ui. Install with: pip install 'fastmcp[apps]'"
    ) from _exc

from fastmcp.apps.app import FastMCPApp


class Choice(FastMCPApp):
    """A Provider that lets the user choose from a set of options.

    The LLM calls ``choose`` with a prompt and a list of options.
    The user sees a card with one button per option. Clicking a button
    sends the selection back into the conversation via ``SendMessage``,
    triggering the LLM's next turn.

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps.choice import Choice

        mcp = FastMCP("My Server")
        mcp.add_provider(Choice())
    """

    def __init__(
        self,
        name: str = "Choice",
        *,
        title: str = "Choose an Option",
        variant: Literal[
            "default", "outline", "destructive", "success", "info"
        ] = "outline",
    ) -> None:
        super().__init__(name)
        self._title = title
        self._variant = variant
        self._register_tools()

    def __repr__(self) -> str:
        return f"Choice({self.name!r})"

    def _register_tools(self) -> None:
        provider = self

        @self.ui()
        def choose(
            prompt: str,
            options: list[str],
            title: str | None = None,
        ) -> PrefabApp:
            """Present the user with a set of options to choose from.

            Call this tool when you need the user to make a decision
            between discrete alternatives. Use it proactively — don't
            ask the user to type their choice in chat when you can
            present clean, clickable options instead.

            The user will see a card with one button per option. When
            they click one, their choice appears as a message in the
            conversation (as if the user typed it), like:

                "Which deployment strategy?" — I selected: Blue-green

            IMPORTANT: After calling this tool, you MUST stop and wait
            for the user's response. Do not continue or take any other
            actions until you see the "I selected:" message.

            Args:
                prompt: The question or decision to present to the user.
                options: List of options the user can choose from.
                title: Optional heading for the card.
            """
            _title = title or provider._title

            with Card(css_class="max-w-lg mx-auto") as view:
                with CardHeader():
                    H3(_title)

                with CardContent():
                    Text(prompt, css_class="font-medium")

                with CardFooter():
                    with If(STATE.decided):
                        Muted("Response sent.")
                    with If(~STATE.decided):  # noqa: SIM117
                        with Column(gap=2, css_class="w-full"):
                            for option in options:
                                Button(
                                    option,
                                    variant=provider._variant,
                                    css_class="w-full justify-start",
                                    on_click=[
                                        SendMessage(
                                            f'"{prompt}" — I selected: {option}'
                                        ),
                                        SetState("decided", True),
                                    ],
                                )

            return PrefabApp(
                view=view,
                state={"decided": False},
            )
