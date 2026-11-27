"""Collect agent feedback through an application-provided handler."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mcp_types import ToolAnnotations

from fastmcp.tools.base import Tool
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)


@dataclass(frozen=True)
class FeedbackReport:
    """A report submitted by an agent using this server."""

    task: str
    description: str
    tool_name: str | None = None
    suggestion: str | None = None


def create_feedback_tool(
    handler: Callable[[FeedbackReport], Awaitable[None] | None],
) -> Tool:
    """Create a `submit_feedback` tool that delivers each report to `handler`."""

    async def submit_feedback(
        task: str,
        description: str,
        tool_name: str | None = None,
        suggestion: str | None = None,
    ) -> str:
        """Report a missing capability, unexpected behavior, or workaround to the server maintainer.

        Submit once per task when you have specific feedback. Omit credentials and personal details.

        Args:
            task: What you were trying to accomplish.
            description: What you tried, what happened, and how it affected the task.
            tool_name: The relevant tool, if one exists.
            suggestion: A change that would have helped, if known.
        """
        report = FeedbackReport(
            task=task,
            description=description,
            tool_name=tool_name,
            suggestion=suggestion,
        )
        if is_coroutine_function(handler):
            result = handler(report)
        else:
            result = await call_sync_fn_in_threadpool(handler, report)
        if inspect.isawaitable(result):
            await result
        return "Feedback received."

    return Tool.from_function(
        submit_feedback,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
