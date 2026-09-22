"""The FastMCP server every downstream smoke script connects to.

python server.py stdio
python server.py http <port> <bearer-token>
python server.py sse <port> <bearer-token>
"""

import base64
import sys
from dataclasses import dataclass

from _harness import INSTRUCTIONS
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    EmbeddedResource,
    InputRequiredResult,
    ResourceLink,
    TextContent,
    TextResourceContents,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.types import Audio, Image

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)
WAV = (
    b"RIFF&\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00"
    b"\x88X\x01\x00\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00"
)


def build(auth_token: str | None = None) -> FastMCP:
    auth = (
        StaticTokenVerifier({auth_token: {"client_id": "smoke", "scopes": []}})
        if auth_token
        else None
    )
    mcp = FastMCP("downstream-smoke", instructions=INSTRUCTIONS, auth=auth)

    class Forecast(BaseModel):
        city: str
        celsius: float
        conditions: list[str]

    @dataclass
    class Approval:
        approved: bool

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

    @mcp.tool
    def snapshot() -> Image:
        """A 1x1 PNG."""
        return Image(data=PNG, format="png")

    @mcp.tool
    def chime() -> Audio:
        """A silent WAV."""
        return Audio(data=WAV, format="wav")

    @mcp.tool
    def attachments() -> list:
        """Mixed content: text, an embedded resource, and a resource link."""
        return [
            TextContent(type="text", text="see attached"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="notes://today", mime_type="text/plain", text="buy milk"
                ),
            ),
            ResourceLink(
                type="resource_link",
                uri="config://app",
                name="app config",
                mime_type="application/json",
            ),
        ]

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
                        "approval": ElicitRequest(
                            method="elicitation/create", params=form
                        )
                    },
                )
            answer = ctx.input_responses["approval"]
            assert isinstance(answer, ElicitResult), answer
            if (
                answer.action == "accept"
                and answer.content
                and answer.content["approved"]
            ):
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

    return mcp


if __name__ == "__main__":
    match sys.argv[1:]:
        case [] | ["stdio"]:
            build().run("stdio", show_banner=False)
        case ["http" | "sse" as transport, port, token]:
            build(token).run(
                transport,
                host="127.0.0.1",
                port=int(port),
                show_banner=False,
                log_level="critical",
            )
        case args:
            sys.exit(f"usage: server.py [stdio | http|sse <port> <token>], got {args}")
