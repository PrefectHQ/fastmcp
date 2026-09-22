"""Serve server.py over streamable HTTP in a subprocess for the length of a `with` block."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SERVER = Path(__file__).with_name("server.py")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def http_server(python: list[str]) -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen([*python, str(SERVER), "http", str(port)], env=os.environ)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                sys.exit(f"server exited with {proc.returncode}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            sys.exit("server did not start listening within 30s")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
