"""Example: Preventing MCP Poisoning with Executable Hash Verification

This example demonstrates how to use hash verification to prevent MCP poisoning
attacks where a malicious actor replaces a legitimate MCP server executable with
a compromised version.
"""

import hashlib
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports.stdio import PythonStdioTransport


def compute_executable_hash(
    executable_path: str | Path, algorithm: str = "sha256"
) -> str:
    """Compute hash of an executable.

    Run this once to get the hash of your trusted executable, then
    use that hash in your client configuration.
    """
    hasher = hashlib.new(algorithm)
    with open(executable_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


async def secure_connection_example():
    """Connect to an MCP server with executable verification."""

    # Step 1: Compute the hash of your trusted Python interpreter
    python_path = sys.executable
    trusted_hash = compute_executable_hash(python_path)

    print(f"Python interpreter: {python_path}")
    print(f"Trusted hash: {trusted_hash}\n")

    # Step 2: Create a transport with hash verification enabled
    transport = PythonStdioTransport(
        script_path="my_mcp_server.py",
        expected_hash=trusted_hash,
        hash_algorithm="sha256",
    )

    # Step 3: Connect - will fail if executable has been modified
    client = Client(transport=transport)
    try:
        async with client:
            print("✓ Connection established - executable verified")
            tools = await client.list_tools()
            print(f"Available tools: {[t.name for t in tools]}")
    except ValueError as e:
        print(f"✗ Security check failed: {e}")


if __name__ == "__main__":
    print("MCP Poisoning Prevention Example")
    print()
    print("To use hash verification:")
    print("1. Compute hash of your trusted executable once")
    print("2. Store the hash securely (config file, environment variable)")
    print("3. Pass expected_hash when creating the transport")
    print()

    python_hash = compute_executable_hash(sys.executable)
    print("Your Python interpreter hash (SHA-256):")
    print(f"  {python_hash}")
