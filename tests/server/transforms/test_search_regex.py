"""Pattern syntax and matching behavior for regex tool search."""

import subprocess
import sys
import textwrap

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.transforms.search import RegexSearchTransform


@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("EMAIL", ["send_email"]),
        ("send.*email|notify", ["send_email"]),
        (r"\bSEND_[a-z]+\b", ["send_email"]),
        ("^send_email", ["send_email"]),
        ("(?-i:SEND_EMAIL)", []),
        ("send(?=_email)", []),
        ("(?<=send_)email", []),
        (r"(email).*\1", []),
        (r".*\Z", []),
        (r"email\.\z", ["send_email"]),
        ("(?a)email", []),
        ("[invalid", []),
    ],
)
async def test_search_pattern_syntax(pattern: str, expected: list[str]) -> None:
    server = FastMCP("search")

    @server.tool
    def send_email() -> str:
        """Send an email and record the email."""
        return "sent"

    server.add_transform(RegexSearchTransform())
    async with Client(server) as client:
        result = await client.call_tool("search_tools", {"pattern": pattern})

    assert result.structured_content is not None
    assert [tool["name"] for tool in result.structured_content["result"]] == expected


@pytest.mark.subprocess_heavy
@pytest.mark.timeout(15)
def test_search_repeated_alternatives() -> None:
    # Run in a child so a regression cannot leave matching running in pytest.
    code = textwrap.dedent(
        """
        import asyncio
        from fastmcp import Client, FastMCP
        from fastmcp.server.transforms.search import RegexSearchTransform

        async def main():
            server = FastMCP("search")

            @server.tool(description="a" * 64 + "!")
            def summarize() -> str:
                return "done"

            server.add_transform(RegexSearchTransform())
            async with Client(server) as client:
                result = await client.call_tool("search_tools", {"pattern": "(a|aa)+$"})
                assert result.structured_content == {"result": []}
                result = await client.call_tool("search_tools", {"pattern": "a+!$"})
                assert result.structured_content is not None
                assert [tool["name"] for tool in result.structured_content["result"]] == ["summarize"]
                assert (await client.call_tool("call_tool", {"name": "summarize", "arguments": {}})).data == "done"

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode == 0, result.stderr
