"""Secure MCP Client with Hash Verification

This example shows how to build a production-ready MCP client that verifies
executable integrity before connecting to servers.
"""

import hashlib
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport


class SecureMCPConfig:
    """Configuration manager for secure MCP connections."""

    def __init__(self, config_file: Path):
        self.config_file = config_file
        self.config: dict = self._load_config()

    def _load_config(self) -> dict:
        if not self.config_file.exists():
            return {"servers": {}}
        return json.loads(self.config_file.read_text())

    def save_config(self) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(self.config, indent=2))

    def add_server(
        self,
        name: str,
        command: str,
        args: list[str],
        expected_hash: str,
        hash_algorithm: str = "sha256",
    ) -> None:
        self.config["servers"][name] = {
            "command": command,
            "args": args,
            "expected_hash": expected_hash,
            "hash_algorithm": hash_algorithm,
        }
        self.save_config()

    def get_server(self, name: str) -> dict | None:
        return self.config["servers"].get(name)


def compute_executable_hash(
    executable_path: str | Path, algorithm: str = "sha256"
) -> str:
    hasher = hashlib.new(algorithm)
    with open(executable_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


async def connect_to_server(config: SecureMCPConfig, server_name: str) -> None:
    """Connect to a configured server with hash verification."""

    server_config = config.get_server(server_name)
    if not server_config:
        raise ValueError(f"Server '{server_name}' not found in configuration")

    transport = StdioTransport(
        command=server_config["command"],
        args=server_config["args"],
        expected_hash=server_config["expected_hash"],
        hash_algorithm=server_config["hash_algorithm"],
    )

    client = Client(transport=transport)
    try:
        async with client:
            print("✓ Connection established - executable verified")
            tools = await client.list_tools()
            for tool in tools:
                print(f"  - {tool.name}: {tool.description}")
    except ValueError as e:
        print(f"✗ Security check failed: {e}")
        raise


if __name__ == "__main__":
    python_hash = compute_executable_hash(sys.executable)
    print("Python interpreter hash (SHA-256):")
    print(f"  Path: {sys.executable}")
    print(f"  Hash: {python_hash}")
    print()
    print("Usage:")
    print("""
    transport = PythonStdioTransport(
        script_path="my_server.py",
        expected_hash="<hash>",
        hash_algorithm="sha256",
    )
    client = Client(transport=transport)

    async with client:
        tools = await client.list_tools()
    """)
