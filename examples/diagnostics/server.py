"""FastMCP Diagnostics Server - for testing tracing, errors, and observability."""

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from fastmcp import FastMCP
from fastmcp.server import create_proxy

ECHO_SERVER_PORT = 8002
ECHO_SERVER_URL = f"http://localhost:{ECHO_SERVER_PORT}/sse"


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Start echo server subprocess and mount proxy to it."""
    echo_path = Path(__file__).parent.parent / "echo.py"

    # Pass OTEL config to subprocess with different service name
    env = os.environ.copy()
    env["OTEL_SERVICE_NAME"] = "fastmcp-echo-server"

    # Start echo server as subprocess using run_with_tracing.py for OTEL export
    run_with_tracing = Path(__file__).parent.parent / "run_with_tracing.py"
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            str(run_with_tracing),
            str(echo_path),
            "--transport",
            "sse",
            "--port",
            str(ECHO_SERVER_PORT),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready (async to avoid blocking event loop)
    async with httpx.AsyncClient() as client:
        for _ in range(50):
            try:
                await client.get(
                    f"http://localhost:{ECHO_SERVER_PORT}/sse", timeout=0.1
                )
                break
            except Exception:
                await asyncio.sleep(0.1)

    # Mount proxy to the running echo server
    echo_proxy = create_proxy(ECHO_SERVER_URL, name="Echo Proxy")
    server.mount(echo_proxy, namespace="proxied")

    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


mcp = FastMCP("Diagnostics Server", lifespan=lifespan)

# === SUCCESSFUL COMPONENTS ===


@mcp.tool
def ping() -> str:
    """Simple ping tool - always succeeds."""
    return "pong"


@mcp.resource("diag://status")
def status_resource() -> str:
    """Status resource - always succeeds."""
    return "OK"


@mcp.resource("diag://echo/{message}")
def echo_template(message: str) -> str:
    """Echo template - always succeeds."""
    return f"Echo: {message}"


@mcp.prompt("greet")
def greet_prompt(name: str = "World") -> str:
    """Greeting prompt - always succeeds."""
    return f"Hello, {name}!"


# === ERROR COMPONENTS ===


@mcp.tool
def fail_tool(message: str = "Intentional tool failure") -> str:
    """Tool that always raises ValueError - for error tracing."""
    raise ValueError(message)


@mcp.resource("diag://error")
def error_resource() -> str:
    """Resource that always raises ValueError."""
    raise ValueError("Intentional resource failure")


@mcp.resource("diag://error/{code}")
def error_template(code: str) -> str:
    """Template that always raises ValueError."""
    raise ValueError(f"Intentional template failure: {code}")


@mcp.prompt("fail")
def fail_prompt() -> str:
    """Prompt that always raises ValueError."""
    raise ValueError("Intentional prompt failure")
