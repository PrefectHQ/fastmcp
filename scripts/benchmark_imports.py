#!/usr/bin/env python
"""Benchmark import times for fastmcp and its dependency chain.

Each measurement runs in a fresh subprocess so there's no shared module cache.
Incremental costs are measured by pre-importing dependencies, so we can see
what each module truly adds.

Usage:
    uv run python scripts/benchmark_imports.py
    uv run python scripts/benchmark_imports.py --runs 10
    uv run python scripts/benchmark_imports.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class BenchmarkCase:
    label: str
    stmt: str
    prereqs: str = ""
    group: str = ""


CASES = [
    # --- Floor ---
    BenchmarkCase("pydantic", "import pydantic", group="floor"),
    BenchmarkCase("mcp", "import mcp", group="floor"),
    BenchmarkCase(
        "mcp (server only)", "import mcp.server.lowlevel.server", group="floor"
    ),
    # --- Auth stack (incremental over mcp) ---
    BenchmarkCase(
        "authlib.jose", "import authlib.jose", prereqs="import mcp", group="auth"
    ),
    BenchmarkCase(
        "cryptography.fernet",
        "from cryptography.fernet import Fernet",
        prereqs="import mcp",
        group="auth",
    ),
    BenchmarkCase(
        "authlib.integrations.httpx_client",
        "from authlib.integrations.httpx_client import AsyncOAuth2Client",
        prereqs="import mcp",
        group="auth",
    ),
    BenchmarkCase(
        "key_value.aio", "import key_value.aio", prereqs="import mcp", group="auth"
    ),
    BenchmarkCase(
        "key_value.aio.stores.filetree",
        "from key_value.aio.stores.filetree import FileTreeStore",
        prereqs="import mcp",
        group="auth",
    ),
    BenchmarkCase("beartype", "import beartype", prereqs="import mcp", group="auth"),
    # --- Docket stack (incremental over mcp) ---
    BenchmarkCase("redis", "import redis", prereqs="import mcp", group="docket"),
    BenchmarkCase(
        "opentelemetry.sdk.metrics",
        "import opentelemetry.sdk.metrics",
        prereqs="import mcp",
        group="docket",
    ),
    BenchmarkCase("docket", "import docket", prereqs="import mcp", group="docket"),
    BenchmarkCase("croniter", "import croniter", prereqs="import mcp", group="docket"),
    # --- Other deps (incremental over mcp) ---
    BenchmarkCase("httpx", "import httpx", prereqs="import mcp", group="other"),
    BenchmarkCase(
        "starlette",
        "from starlette.applications import Starlette",
        prereqs="import mcp",
        group="other",
    ),
    BenchmarkCase(
        "pydantic_settings",
        "import pydantic_settings",
        prereqs="import mcp",
        group="other",
    ),
    BenchmarkCase(
        "rich.console", "import rich.console", prereqs="import mcp", group="other"
    ),
    BenchmarkCase("jsonref", "import jsonref", prereqs="import mcp", group="other"),
    BenchmarkCase("requests", "import requests", prereqs="import mcp", group="other"),
    # --- FastMCP (total and incremental) ---
    BenchmarkCase("fastmcp (total)", "from fastmcp import FastMCP", group="fastmcp"),
    BenchmarkCase(
        "fastmcp (over mcp)",
        "from fastmcp import FastMCP",
        prereqs="import mcp",
        group="fastmcp",
    ),
    BenchmarkCase(
        "fastmcp (over mcp+docket)",
        "from fastmcp import FastMCP",
        prereqs="import mcp; import docket",
        group="fastmcp",
    ),
    BenchmarkCase(
        "fastmcp (over mcp+docket+auth deps)",
        "from fastmcp import FastMCP",
        prereqs=(
            "import mcp; import docket; import authlib.jose;"
            " from cryptography.fernet import Fernet;"
            " import key_value.aio"
        ),
        group="fastmcp",
    ),
]


def measure_once(stmt: str, prereqs: str) -> float | None:
    pre = prereqs + "; " if prereqs else ""
    code = (
        f"{pre}"
        "import time as _t; _s=_t.perf_counter(); "
        f"{stmt}; "
        "print(f'{(_t.perf_counter()-_s)*1000:.2f}')"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0 and r.stdout.strip():
        return float(r.stdout.strip())
    return None


def measure(case: BenchmarkCase, runs: int) -> dict[str, float | str | None]:
    times: list[float] = []
    for _ in range(runs):
        t = measure_once(case.stmt, case.prereqs)
        if t is not None:
            times.append(t)

    if not times:
        return {"label": case.label, "group": case.group, "median_ms": None}

    times.sort()
    median = times[len(times) // 2]
    return {
        "label": case.label,
        "group": case.group,
        "median_ms": round(median, 1),
        "min_ms": round(times[0], 1),
        "max_ms": round(times[-1], 1),
        "runs": len(times),
    }


def print_table(results: list[dict[str, float | str | None]]) -> None:
    current_group = None
    print(f"\n{'Module':<45} {'Median':>8} {'Min':>8} {'Max':>8}")
    print("-" * 71)
    for r in results:
        if r["group"] != current_group:
            current_group = r["group"]
            group_labels = {
                "floor": "--- Unavoidable floor ---",
                "auth": "--- Auth stack (incremental over mcp) ---",
                "docket": "--- Docket stack (incremental over mcp) ---",
                "other": "--- Other deps (incremental over mcp) ---",
                "fastmcp": "--- FastMCP totals ---",
            }
            print(f"\n{group_labels.get(current_group, current_group)}")
        if r["median_ms"] is not None:
            print(
                f"  {r['label']:<43} {r['median_ms']:>7.1f}ms"
                f" {r['min_ms']:>7.1f}ms {r['max_ms']:>7.1f}ms"
            )
        else:
            print(f"  {r['label']:<43}    error")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark fastmcp import times")
    parser.add_argument(
        "--runs", type=int, default=5, help="Number of runs per measurement (default 5)"
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    print(f"Benchmarking import times ({args.runs} runs each)...")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Executable: {sys.executable}")

    results = []
    for case in CASES:
        r = measure(case, args.runs)
        results.append(r)
        if not args.json:
            ms = f"{r['median_ms']:.1f}ms" if r["median_ms"] is not None else "error"
            print(f"  {case.label}: {ms}")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_table(results)


if __name__ == "__main__":
    main()
