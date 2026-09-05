"""Subprocess target for server signal-shutdown tests."""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio

from fastmcp import FastMCP


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--transport", choices=("http", "sse"), required=True)
    args = parser.parse_args()

    def record(event: str) -> None:
        with args.events.open("a") as events:
            events.write(event + "\n")

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        record("startup")
        try:
            yield {}
        finally:
            # Exercise asynchronous cleanup, not only a synchronous marker.
            await anyio.sleep(0.05)
            record("shutdown")

    server = FastMCP("signal-shutdown-test", lifespan=lifespan)

    @server.tool
    def ping() -> str:
        return "pong"

    server.run(
        transport=args.transport,
        host="127.0.0.1",
        port=args.port,
        show_banner=False,
        log_level="error",
    )


if __name__ == "__main__":
    main()
