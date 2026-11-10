#!/usr/bin/env python
"""Benchmark FastMCP's HTTP server cold-start path in fresh interpreters.

The benchmark separates the work users pay before an HTTP server can accept
requests:

1. import the public ``FastMCP`` entry point;
2. construct a server and register representative tools;
3. build the Streamable HTTP ASGI application.

Every sample runs in a fresh interpreter. Use ratios and the shape of the
results rather than treating single-machine absolute timings as universal.

Usage:
    uv run python scripts/benchmark_http_startup.py
    uv run python scripts/benchmark_http_startup.py --runs 10
    uv run python scripts/benchmark_http_startup.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from typing import TypedDict


class Sample(TypedDict):
    import_ms: float
    server_ms: float
    app_ms: float
    total_ms: float
    module_count: int
    rss_mib: float
    heavy_module_counts: dict[str, int]


_PROBE = textwrap.dedent(
    """
    import json
    import resource
    import sys
    import time

    started = time.perf_counter()
    from fastmcp import FastMCP
    imported = time.perf_counter()

    server = FastMCP("HTTP cold-start benchmark")

    def make_tool(index):
        def tool(value: int = index) -> int:
            return value

        tool.__name__ = f"tool_{index}"
        return tool

    for index in range(10):
        server.tool(make_tool(index))
    configured = time.perf_counter()

    app = server.http_app(transport="http", stateless_http=True)
    assert app is not None
    ready = time.perf_counter()

    heavy_roots = {
        "authlib",
        "cryptography",
        "httpx2",
        "key_value",
        "mcp",
        "mcp_types",
        "opentelemetry",
        "pydantic",
        "rich",
        "sse_starlette",
        "starlette",
        "uvicorn",
    }
    heavy_module_counts = {
        root: sum(
            module == root or module.startswith(f"{root}.") for module in sys.modules
        )
        for root in sorted(heavy_roots)
    }
    heavy_module_counts = {
        root: count for root, count in heavy_module_counts.items() if count
    }

    print(
        json.dumps(
            {
                "import_ms": (imported - started) * 1000,
                "server_ms": (configured - imported) * 1000,
                "app_ms": (ready - configured) * 1000,
                "total_ms": (ready - started) * 1000,
                "module_count": len(sys.modules),
                "rss_mib": (
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    / (1024 * 1024)
                    if sys.platform == "darwin"
                    else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                ),
                "heavy_module_counts": heavy_module_counts,
            }
        )
    )
    """
)


def _sample() -> Sample:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


def _median(samples: Sequence[Sample], key: str) -> float:
    return statistics.median(float(sample[key]) for sample in samples)  # type: ignore[literal-required]


def _summarize(samples: list[Sample]) -> dict[str, object]:
    return {
        "runs": len(samples),
        "import_ms": round(_median(samples, "import_ms"), 1),
        "server_ms": round(_median(samples, "server_ms"), 1),
        "app_ms": round(_median(samples, "app_ms"), 1),
        "total_ms": round(_median(samples, "total_ms"), 1),
        "module_count": round(_median(samples, "module_count")),
        "rss_mib": round(_median(samples, "rss_mib"), 1),
        "heavy_module_counts": samples[-1]["heavy_module_counts"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    samples = [_sample() for _ in range(args.runs)]
    summary = _summarize(samples)
    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print(f"Python: {sys.version.split()[0]}")
    print(f"Runs: {summary['runs']}")
    print(f"Import FastMCP: {summary['import_ms']:.1f} ms")
    print(f"Construct + 10 tools: {summary['server_ms']:.1f} ms")
    print(f"Build HTTP app: {summary['app_ms']:.1f} ms")
    print(f"Total to ASGI app: {summary['total_ms']:.1f} ms")
    print(f"Modules: {summary['module_count']}")
    print(f"Peak RSS: {summary['rss_mib']:.1f} MiB")


if __name__ == "__main__":
    main()
