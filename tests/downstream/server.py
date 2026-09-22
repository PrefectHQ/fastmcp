"""The FastMCP server every downstream smoke script connects to.

Run as `python server.py stdio` or `python server.py http <port>`.
"""

import sys
from dataclasses import dataclass

from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP(
    "downstream-smoke", instructions="Arithmetic and weather for smoke tests."
)


class Forecast(BaseModel):
    city: str
    celsius: float
    conditions: list[str]


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool
def forecast(city: str, days: int = 1) -> Forecast:
    """Structured weather forecast for a city."""
    return Forecast(city=city, celsius=21.5, conditions=["sunny"] * days)


@mcp.tool
def divide(a: float, b: float) -> float:
    """Divide a by b."""
    if b == 0:
        raise ToolError("cannot divide by zero")
    return a / b


@mcp.tool
async def count_to(n: int, ctx: Context) -> str:
    """Count to n, reporting progress and logging along the way."""
    for i in range(1, n + 1):
        await ctx.report_progress(i, n)
    await ctx.info(f"counted to {n}")
    return f"counted to {n}"


@dataclass
class Approval:
    approved: bool


@mcp.tool
async def confirm(action: str, ctx: Context) -> str | InputRequiredResult:
    """Ask the client to approve an action, using whichever elicitation path the connection's protocol era supports."""
    rc = ctx.request_context
    if rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS:
        if ctx.input_responses is None:
            form = ElicitRequestFormParams(
                message=f"approve {action}?",
                requested_schema={
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                },
            )
            return InputRequiredResult(
                result_type="input_required",
                input_requests={
                    "approval": ElicitRequest(method="elicitation/create", params=form)
                },
            )
        answer = ctx.input_responses["approval"]
        assert isinstance(answer, ElicitResult), answer
        if answer.action == "accept" and answer.content and answer.content["approved"]:
            return f"{action}: approved"
        return f"{action}: {answer.action}"
    result = await ctx.elicit(f"approve {action}?", response_type=Approval)
    if result.action == "accept" and result.data.approved:
        return f"{action}: approved"
    return f"{action}: {result.action}"


@mcp.resource("config://app", mime_type="application/json")
def app_config() -> str:
    return '{"mode": "smoke"}'


@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    return f"hello, {name}"


@mcp.prompt
def review(code: str) -> str:
    """Ask for a code review."""
    return f"please review:\n{code}"


if __name__ == "__main__":
    if sys.argv[1:2] == ["http"]:
        mcp.run("http", host="127.0.0.1", port=int(sys.argv[2]), show_banner=False)
    else:
        mcp.run("stdio", show_banner=False)
