import os
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

PYTHON_EXE = sys.executable

SERVER_CODE = """
import uvicorn
from fastapi import FastAPI
from fastmcp import FastMCP

mcp = FastMCP()

@mcp.tool("dummy_tool", "A simple dummy tool for the test server")
def add(a: int, b: int) -> int:
    return a + b

app = FastAPI() # Intentionally no lifespan=mcp.lifespan
app.mount("/", mcp.http_app(transport="streamable-http"))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None)
"""


@pytest.mark.timeout(20)
def test_server_shows_informative_error_on_stderr():
    """
    Runs a minimal FastMCP+FastAPI server (that omits lifespan wiring)
    as a subprocess, triggers the error via an HTTP request, and then checks
    if the server's stderr contains the specific informative error message for issue #518.
    """
    process = None
    captured_stderr = ""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as tmp_script:
        tmp_script.write(SERVER_CODE)
        tmp_script_path = tmp_script.name

    try:
        process = subprocess.Popen(
            [PYTHON_EXE, "-u", tmp_script_path],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            universal_newlines=True,
        )

        time.sleep(3)

        if process.poll() is None:
            try:
                with httpx.Client(timeout=5.0) as client:
                    # The mounted FastMCP app is at root, its internal default path is /mcp
                    client.get("http://localhost:8080/mcp/")
            except httpx.RequestError:
                pass
            time.sleep(1)

    finally:
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                _, captured_stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _, captured_stderr = process.communicate()

        if os.path.exists(tmp_script_path):
            os.unlink(tmp_script_path)

    assert captured_stderr is not None, "stderr should have been captured"
    normalized_stderr = captured_stderr.replace("\r\n", "\n").replace("\r", "\n")

    assert (
        "FastMCP's StreamableHTTPSessionManager task group was not initialized"
        in normalized_stderr
    )
    assert "lifespan=mcp_app.lifespan" in normalized_stderr
    assert "gofastmcp.com/deployment/asgi" in normalized_stderr
    assert "Original error: Task group is not initialized" in normalized_stderr
