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

    The message appears as if the user sent it, so the LLM sees
    something like ``'"Deploy v3.2 to production" is APPROVED'``.

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps.approval import Approval

        mcp = FastMCP("My Server")
        mcp.add_provider(Approval())

    Customized::

        Approval(
            title="Deploy Gate",
            approve_text="Ship it",
            approve_variant="default",
            reject_text="Abort",
            reject_variant="destructive",
        )
    """

    def __init__(
        self,
        name: str = "Approval",
        *,
        title: str = "Approval Required",
        approve_text: str = "Approve",
        reject_text: str = "Reject",
        approve_variant: Literal[
            "default", "destructive", "success", "info"
        ] = "default",
        reject_variant: Literal[
            "default", "outline", "destructive", "success", "info"
        ] = "outline",
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
            title: str | None = None,
            approve_text: str | None = None,
            reject_text: str | None = None,
            approve_variant: str | None = None,
            reject_variant: str | None = None,
        ) -> PrefabApp:
            """Request human approval before proceeding with an action.

            Call this tool proactively whenever you are about to take a
            significant or irreversible action and want the user to
            confirm first. Do NOT wait for the user to ask you to seek
            approval — use your judgment about when confirmation is
            appropriate.

            The user will see an approval card with the summary, optional
            details, and Approve/Reject buttons. When they click a button,
            their decision appears as a message in the conversation (as if
            the user typed it), like:

                "Deploy v3.2 to production" — I selected: Approve

            or:

                "Deploy v3.2 to production" — I selected: Reject

            IMPORTANT: After calling this tool, you MUST stop and wait
            for the user's response. Do not continue, do not take any
            other actions, do not generate further output until you see
            the "I selected:" message. If approved, continue with the
            action. If rejected, acknowledge and ask how to proceed.

            Args:
                summary: Brief description of the action requiring approval
                    (shown prominently to the user).
                details: Optional longer explanation, context, or
                    consequences of the action.
                title: Heading for the approval card (default: "Approval Required").
                approve_text: Label for the approve button (default: "Approve").
                reject_text: Label for the reject button (default: "Reject").
                approve_variant: Button style — "default", "destructive",
                    "success", or "info".
                reject_variant: Button style for the reject button
                    (same options plus "outline").
            """
            _title = title or provider._title
            _approve = approve_text or provider._approve_text
            _reject = reject_text or provider._reject_text
            _approve_v = approve_variant or provider._approve_variant
            _reject_v = reject_variant or provider._reject_variant

            approve_msg = f'"{summary}" — I selected: {_approve}'
            reject_msg = f'"{summary}" — I selected: {_reject}'

            with Card(css_class="max-w-lg mx-auto") as view:
                with CardHeader():
                    H3(_title)

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
                                _reject,
                                variant=_reject_v,
                                on_click=[
                                    SendMessage(reject_msg),
                                    SetState("decided", True),
                                ],
                            )
                            Button(
                                _approve,
                                variant=_approve_v,
                                on_click=[
                                    SendMessage(approve_msg),
                                    SetState("decided", True),
                                ],
                            )

            return PrefabApp(
                view=view,
                state={"decided": False},
            )
