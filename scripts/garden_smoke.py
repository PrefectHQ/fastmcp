#!/usr/bin/env python
"""Smoke-test an installed FastMCP, as a user gets it from PyPI.

Run inside an environment that has only the published packages, never the
checkout, so nothing here may import from the repository:

    uv run --isolated --no-project --no-config --prerelease=allow \
        --with fastmcp==4.2.0a1 python scripts/garden_smoke.py

Prints one JSON object: the installed versions, and each check's name,
outcome, detail, and seconds. Exits 1 if any check fails.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from pathlib import Path

SERVER = textwrap.dedent(
    """
    import sys

    from fastmcp import FastMCP

    mcp = FastMCP("garden")


    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b


    @mcp.resource("greeting://{name}")
    def greeting(name: str) -> str:
        return f"hello {name}"


    @mcp.prompt
    def review(code: str) -> str:
        return f"review this: {code}"


    if __name__ == "__main__":
        if sys.argv[1:2] == ["http"]:
            mcp.run(transport="http", port=int(sys.argv[2]), show_banner=False)
        else:
            mcp.run(show_banner=False)
    """
)

PACKAGES = ["fastmcp", "fastmcp-slim", "fastmcp-tasks", "fastmcp-remote", "mcp"]


async def roundtrip(client) -> str:
    async with client:
        tools = {tool.name for tool in await client.list_tools()}
        assert "add" in tools, f"tools: {sorted(tools)}"
        result = await client.call_tool("add", {"a": 2, "b": 3})
        assert result.data == 5, f"add returned {result.data!r}"
        contents = await client.read_resource("greeting://garden")
        text = contents[0].text
        assert text == "hello garden", f"resource returned {text!r}"
        prompt = await client.get_prompt("review", {"code": "x = 1"})
        assert prompt.messages, "prompt returned no messages"
        # A second call on the same session catches transports that break
        # after their first request.
        again = await client.call_tool("add", {"a": 1, "b": 1})
        assert again.data == 2, f"second add returned {again.data!r}"
    return "tool, resource template, prompt, repeat call"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check_in_memory(server_file: Path) -> str:
    from fastmcp import Client

    namespace: dict = {"__name__": "garden_server"}
    exec(server_file.read_text(), namespace)
    return await roundtrip(Client(namespace["mcp"]))


async def check_stdio(server_file: Path) -> str:
    from fastmcp import Client

    return await roundtrip(Client(server_file))


async def check_http(server_file: Path) -> str:
    from fastmcp import Client

    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(server_file), "http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    # Stop the child first: reading stderr from a live
                    # process blocks until it exits.
                    process.kill()
                    _, stderr = process.communicate(timeout=10)
                    raise RuntimeError(
                        f"server did not start: {stderr.decode()[-500:]}"
                    )
                await asyncio.sleep(0.1)
        return await roundtrip(Client(f"http://127.0.0.1:{port}/mcp"))
    finally:
        process.terminate()
        process.wait(timeout=10)


def check_cli() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "fastmcp.cli", "version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-500:] or result.stdout[-500:])
    first = result.stdout.strip().splitlines()[0] if result.stdout else "ok"
    return " ".join(first.split())


def run(name: str, check, *args) -> dict:
    start = time.perf_counter()
    try:
        value = check(*args)
        if asyncio.iscoroutine(value):
            value = asyncio.run(asyncio.wait_for(value, timeout=30))
        outcome, detail = "pass", value
    except Exception as exc:
        outcome = "fail"
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return {
        "check": name,
        "outcome": outcome,
        "detail": detail,
        "seconds": round(time.perf_counter() - start, 2),
    }


def main() -> int:
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None

    with tempfile.TemporaryDirectory() as tmp:
        server_file = Path(tmp) / "server.py"
        server_file.write_text(SERVER)
        start = time.perf_counter()
        checks = [
            run("import", lambda: __import__("fastmcp").__version__),
            run("cli version", check_cli),
            run("in-memory", check_in_memory, server_file),
            run("stdio", check_stdio, server_file),
            run("streamable http", check_http, server_file),
        ]
        total = round(time.perf_counter() - start, 2)

    print(
        json.dumps({"versions": versions, "checks": checks, "seconds": total}, indent=2)
    )
    return 1 if any(check["outcome"] == "fail" for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
