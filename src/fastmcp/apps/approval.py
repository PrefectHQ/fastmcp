"""Approval — a Provider that adds human-in-the-loop approval to any server.

The LLM presents a summary of what it's about to do, and the user
approves or rejects via buttons. The result is sent back into the
conversation as a message, prompting the LLM's next turn.

Requires ``fastmcp[apps]`` (prefab-ui).

Usage::

    from fastmcp import FastMCP
    from fastmcp.apps.approval import Approval

    mcp = FastMCP("My Server")
    mcp.add_provider(Approval())
"""

from __future__ import annotations

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
        Row,
        Text,
    )
    from prefab_ui.components.control_flow import If
    from prefab_ui.rx import STATE
except ImportError as _exc:
    raise ImportError(
        "Approval requires prefab-ui. Install with: pip install 'fastmcp[apps]'"
    ) from _exc


from fastmcp.apps.app import FastMCPApp


class Approval(FastMCPApp):
    """A Provider that adds human-in-the-loop approval to a server.

    The LLM calls the ``request_approval`` tool with a summary and
    optional details. The user sees an approval card with Approve and
    Reject buttons. Clicking either sends a message back into the
    conversation (via ``SendMessage``), triggering the LLM's next turn.

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps.approval import Approval

        mcp = FastMCP("My Server")
        mcp.add_provider(Approval())
    """

    def __init__(
        self,
        name: str = "Approval",
        *,
        title: str = "Approval Required",
        approve_text: str = "Approve",
        reject_text: str = "Reject",
        approve_variant: str = "default",
        reject_variant: str = "outline",
    ) -> None:
        super().__init__(name)
        self._title = title
        self._approve_text = approve_text
        self._reject_text = reject_text
        self._approve_variant = approve_variant
        self._reject_variant = reject_variant
        self._register_tools()

    def __repr__(self) -> str:
        return f"Approval({self.name!r})"

    def _register_tools(self) -> None:
        provider = self

        @self.ui()
        def request_approval(
            summary: str,
            details: str | None = None,
        ) -> PrefabApp:
            """Request human approval before proceeding.

            Shows the user what's about to happen and lets them
            approve or reject. The decision is sent as a message
            back into the conversation.

            Args:
                summary: Brief description of the action requiring approval.
                details: Optional longer explanation or context.
            """
            with Card(css_class="max-w-lg mx-auto") as view:
                with CardHeader():
                    H3(provider._title)

                with CardContent(), Column(gap=3):
                    Text(summary, css_class="font-medium")
                    if details:
                        Muted(details)

                with CardFooter():
                    with If(STATE.decided):
                        Muted("Response sent.")
                    with If(~STATE.decided):  # noqa: SIM117
                        with Row(gap=2, css_class="w-full justify-end"):
                            Button(
                                provider._reject_text,
                                variant=provider._reject_variant,
                                on_click=[
                                    SendMessage(f"Rejected: {summary}"),
                                    SetState("decided", True),
                                ],
                            )
                            Button(
                                provider._approve_text,
                                variant=provider._approve_variant,
                                on_click=[
                                    SendMessage(f"Approved: {summary}"),
                                    SetState("decided", True),
                                ],
                            )

            return PrefabApp(
                view=view,
                state={"decided": False},
            )
