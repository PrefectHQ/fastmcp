"""Shared plumbing for the downstream smoke scripts: server processes and reporting."""

import os
import secrets
import socket
import subprocess
import sys
import time
import traceback
import warnings
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
SERVER = HERE / "server.py"
REPO = HERE.parents[1]
INSTRUCTIONS = "Arithmetic and weather for smoke tests."
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def server_python() -> str:
    """The interpreter of the FastMCP checkout, which is what serves every smoke test."""
    return os.environ.get("FASTMCP_SERVER_PYTHON") or str(
        REPO / ".venv" / "bin" / "python"
    )


def server_env() -> dict[str, str]:
    """Keep the server's own logs and warnings out of the consumer's output."""
    return {**os.environ, "FASTMCP_LOG_LEVEL": "CRITICAL", "PYTHONWARNINGS": "ignore"}


def stdio_command() -> tuple[str, list[str]]:
    return server_python(), [str(SERVER), "stdio"]


@dataclass
class Server:
    url: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@contextmanager
def serve(transport: str) -> Iterator[Server]:
    """Run server.py over `http` or `sse` with bearer auth for the length of the block."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(16)
    proc = subprocess.Popen(
        [server_python(), str(SERVER), transport, str(port), token], env=server_env()
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"{transport} server exited with {proc.returncode}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{transport} server did not listen within 30s"
                    ) from None
                time.sleep(0.1)
        path = "/sse" if transport == "sse" else "/mcp"
        yield Server(url=f"http://127.0.0.1:{port}{path}", token=token)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@dataclass
class Result:
    transport: str
    check: str
    error: str | None
    seconds: float


@dataclass
class Report:
    consumer: str
    versions: dict[str, str]
    results: list[Result] = field(default_factory=list)
    warnings: dict[str, str] = field(default_factory=dict)
    _transport: str = ""

    @contextmanager
    def transport(self, name: str) -> Iterator[None]:
        self._transport = name
        print(
            f"::group::{self.consumer} via {name}"
            if IN_ACTIONS
            else f"\n{self.consumer} via {name}"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                yield
            except Exception as error:
                self._record("connect", error, 0.0)
        for w in caught:
            if issubclass(w.category, (DeprecationWarning, UserWarning)):
                self.warnings.setdefault(f"{w.category.__name__}: {w.message}", name)
        if IN_ACTIONS:
            print("::endgroup::")

    async def check(self, name: str, fn: Callable[[], Awaitable[object]]) -> None:
        start = time.monotonic()
        try:
            await fn()
            error = None
        except Exception as exc:
            error = exc
        self._record(name, error, time.monotonic() - start)

    def _record(self, name: str, error: BaseException | None, seconds: float) -> None:
        detail = None if error is None else f"{type(error).__name__}: {error}"
        self.results.append(Result(self._transport, name, detail, seconds))
        mark = "✓" if error is None else "✗"
        print(
            f"  {mark} {name:<34} {seconds * 1000:6.0f} ms"
            + ("" if error is None else f"\n      {detail}")
        )
        if error is not None:
            traceback.print_exception(error, file=sys.stdout)

    def finish(self) -> None:
        failed = [r for r in self.results if r.error]
        transports = list(dict.fromkeys(r.transport for r in self.results))
        checks = list(dict.fromkeys(r.check for r in self.results))
        cell = {
            (r.transport, r.check): ("✅" if not r.error else "❌")
            for r in self.results
        }

        lines = [
            f"### {'❌' if failed else '✅'} {self.consumer}",
            "",
            " · ".join(f"`{k} {v}`" for k, v in self.versions.items()),
            "",
            "| check | " + " | ".join(transports) + " |",
            "|---|" + "---|" * len(transports),
            *(
                f"| {c} | "
                + " | ".join(cell.get((t, c), "—") for t in transports)
                + " |"
                for c in checks
            ),
        ]
        if failed:
            lines += ["", "**Failures**", ""]
            lines += [f"- `{r.transport}` / `{r.check}`: {r.error}" for r in failed]
        if self.warnings:
            lines += [
                "",
                "<details><summary>Warnings raised in the consumer's process</summary>",
                "",
            ]
            lines += [
                f"- `{where}`: {message}" for message, where in self.warnings.items()
            ]
            lines += ["", "</details>"]
        summary = "\n".join(lines) + "\n"

        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        for message, where in self.warnings.items():
            print(
                f"::warning title={self.consumer} via {where}::{message}"
                if IN_ACTIONS
                else f"warning via {where}: {message}"
            )
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(path, "a") as fh:
                fh.write(summary)
        if failed:
            sys.exit(1)


def junit_summary(path: str, title: str) -> None:
    """Append a pass/fail line and any failures from a pytest JUnit report to the job summary."""
    import xml.etree.ElementTree as ET

    suite = ET.parse(path).getroot()
    suite = suite if suite.tag == "testsuite" else suite[0]
    total, failures, errors, skipped = (
        int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")
    )
    failed = [
        f"- `{case.get('classname')}::{case.get('name')}`"
        for case in suite.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    mark = "❌" if failed else "✅"
    lines = [
        f"### {mark} {title}",
        "",
        f"{total - failures - errors - skipped} passed, {failures + errors} failed, {skipped} skipped",
        *(["", *failed] if failed else []),
    ]
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    junit_summary(sys.argv[1], sys.argv[2])
