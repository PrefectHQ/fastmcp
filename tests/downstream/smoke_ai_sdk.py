"""Drive a FastMCP server through the Vercel AI SDK's MCP client.

The AI SDK's `@ai-sdk/mcp` is a TypeScript MCP client of its own, for both
protocol eras, rather than the `mcp` Python SDK every other smoke shares with
FastMCP, so it checks FastMCP's wire format against an implementation that was
written separately. This script starts the servers and runs `ai_sdk/smoke.mjs`
once per connection; it needs Node and the AI SDK installed in `ai_sdk/`:

    npm install --prefix tests/downstream/ai_sdk --no-save @ai-sdk/mcp ai zod
    python tests/downstream/smoke_ai_sdk.py
"""

import json
import os
import shutil
import subprocess
from typing import Any

from _harness import (
    HERE,
    INSTRUCTIONS,
    LEGACY_PROXY_GAPS,
    PROXY_GAPS,
    TEMPLATE_READS,
    TOOLS,
    Report,
    serve,
    server_env,
    stdio_command,
)

AI_SDK = HERE / "ai_sdk"


class AISDKCheckFailed(Exception):
    """A check that failed inside the Node process, carrying its error text."""


def installed(package: str) -> str:
    manifest = AI_SDK / "node_modules" / package / "package.json"
    return json.loads(manifest.read_text())["version"]


def run(report: Report, config: dict[str, Any], gaps: dict[str, str]) -> None:
    config = {
        **config,
        "gaps": gaps,
        "instructions": INSTRUCTIONS,
        "tools": sorted(TOOLS),
        "template_reads": TEMPLATE_READS,
    }
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not on PATH")
    proc = subprocess.run(
        [node, str(AI_SDK / "smoke.mjs"), json.dumps(config)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=AI_SDK,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    for line in lines:
        result = json.loads(line)
        error = AISDKCheckFailed(result["error"]) if result["error"] else None
        report.record(
            result["check"], error, result["ms"] / 1000, known_gap=result["gap"]
        )
    if proc.returncode != 0 or not lines:
        report.record(
            "node process",
            AISDKCheckFailed(
                f"exited with {proc.returncode}: {proc.stderr.strip()[-2000:]}"
            ),
            0.0,
        )


def main() -> None:
    report = Report(
        "Vercel AI SDK",
        {"@ai-sdk/mcp": installed("@ai-sdk/mcp"), "ai": installed("ai")},
    )
    command, args = stdio_command()
    # The AI SDK's stdio transport passes the server only a short default list
    # of variables plus the ones it is given, so give it the smoke server env.
    stdio = {"transport": "stdio", "command": command, "args": args}
    stdio["env"] = server_env()

    for era in ("legacy", "modern"):
        with report.transport(f"stdio, {era} era"):
            run(report, {**stdio, "era": era}, {})

    for transport, label, eras in (
        ("http", "streamable HTTP", ("legacy", "modern")),
        # SSE is a handshake-era transport only.
        ("sse", "SSE", ("legacy",)),
        ("proxy", "FastMCP proxy", ("legacy", "modern")),
    ):
        with serve(transport) as server:
            for era in eras:
                gaps: dict[str, str] = {}
                if transport == "proxy":
                    gaps = LEGACY_PROXY_GAPS if era == "legacy" else PROXY_GAPS
                with report.transport(f"{label}, {era} era"):
                    run(
                        report,
                        {
                            "transport": "http" if transport == "proxy" else transport,
                            "url": server.url,
                            "headers": server.headers,
                            "era": era,
                        },
                        gaps,
                    )

    report.finish()


if __name__ == "__main__":
    os.environ.setdefault("NODE_NO_WARNINGS", "1")
    main()
