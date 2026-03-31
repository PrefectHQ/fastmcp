"""Demo server for testing the dev apps MCP message inspector.

Exercises tool calls, server notifications (ctx.log), and errors
so you can verify all message types appear in the inspector panel.

Usage:
    fastmcp dev apps examples/apps/inspector_demo.py
"""

from __future__ import annotations

from prefab_ui.actions import ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage, UpdateContext
from prefab_ui.components import (
    Badge,
    Button,
    Column,
    Heading,
    Muted,
    Row,
)
from prefab_ui.rx import ERROR

from fastmcp import FastMCP
from fastmcp.server.context import Context

mcp = FastMCP("Inspector Demo")


@mcp.tool(app=True)
def demo() -> Column:
    """A demo app that exercises various MCP message types."""
    with Column(gap=6, css_class="p-8 max-w-lg") as view:
        Heading("Inspector Demo")
        Muted("Click the buttons and watch the inspector panel on the right.")

        with Column(gap=3):
            with Row(gap=2, align="center"):
                Button(
                    "Call Tool",
                    variant="default",
                    on_click=CallTool(
                        "echo",
                        arguments={"message": "Hello from the inspector!"},
                        on_success=ShowToast("Tool call succeeded", variant="success"),
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                )
                Badge("tools/call + response", variant="secondary")

            with Row(gap=2, align="center"):
                Button(
                    "Call with Logging",
                    variant="default",
                    on_click=CallTool(
                        "echo_with_logs",
                        arguments={"message": "Watch the notifications!"},
                        on_success=ShowToast("Done (check logs)", variant="success"),
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                )
                Badge("tools/call + notifications", variant="secondary")

            with Row(gap=2, align="center"):
                Button(
                    "Trigger Error",
                    variant="destructive",
                    on_click=CallTool(
                        "fail",
                        arguments={},
                        on_error=ShowToast(ERROR, variant="error"),
                    ),
                )
                Badge("error response", variant="destructive")

            with Row(gap=2, align="center"):
                Button(
                    "Update Context",
                    variant="outline",
                    on_click=[
                        UpdateContext(content="Demo context from inspector"),
                        ShowToast("Context updated", variant="success"),
                    ],
                )
                Badge("bridge: UpdateContext", variant="outline")

            with Row(gap=2, align="center"):
                Button(
                    "Send Message",
                    variant="outline",
                    on_click=SendMessage("Tell me about this demo app"),
                )
                Badge("bridge: SendMessage", variant="outline")

    return view


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back."""
    return f"Echo: {message}"


@mcp.tool()
async def echo_with_logs(message: str, ctx: Context) -> str:
    """Echo a message and emit log notifications."""
    await ctx.log(f"Processing: {message}", level="info")
    await ctx.log("Step 1: validated input", level="debug")
    await ctx.log("Step 2: generating response", level="debug")
    return f"Logged echo: {message}"


@mcp.tool()
def fail() -> str:
    """Always raises an error."""
    raise ValueError("This is a deliberate error for testing the inspector")


if __name__ == "__main__":
    mcp.run()
