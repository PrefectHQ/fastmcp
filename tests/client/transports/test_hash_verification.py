"""Tests for executable hash verification in stdio transports."""

import hashlib
import sys
from pathlib import Path

import pytest

from fastmcp.client.transports.stdio import (
    HashAlgorithm,
    PythonStdioTransport,
    _verify_executable_hash,
)


def compute_file_hash(path: Path, algorithm: str = "sha256") -> str:
    """Compute hash of a file."""
    hasher = hashlib.new(algorithm)
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def test_verify_hash_success():
    """Test that verification passes when hash matches."""
    python_path = Path(sys.executable).resolve()
    expected_hash = compute_file_hash(python_path)
    # Should not raise
    _verify_executable_hash(sys.executable, expected_hash, "sha256", None, None)


def test_verify_hash_failure():
    """Test that verification fails when hash doesn't match."""
    with pytest.raises(ValueError, match="Executable hash mismatch"):
        _verify_executable_hash(sys.executable, "0" * 64, "sha256", None, None)


def test_verify_hash_different_algorithms():
    """Test hash verification with different algorithms."""
    python_path = Path(sys.executable).resolve()
    algorithms: list[HashAlgorithm] = ["sha256", "sha512", "md5"]
    for algorithm in algorithms:
        expected_hash = compute_file_hash(python_path, algorithm)
        _verify_executable_hash(sys.executable, expected_hash, algorithm, None, None)


def test_verify_hash_file_not_found():
    """Test that verification fails for missing executables."""
    with pytest.raises(FileNotFoundError):
        _verify_executable_hash("/nonexistent/binary", "abc", "sha256", None, None)


@pytest.mark.asyncio
async def test_transport_hash_mismatch_prevents_connect(tmp_path: Path):
    """Test that transport refuses to connect when hash doesn't match."""
    script = tmp_path / "server.py"
    script.write_text("print('hello')")

    transport = PythonStdioTransport(
        script_path=script,
        python_cmd=sys.executable,
        expected_hash="0" * 64,
        hash_algorithm="sha256",
    )

    with pytest.raises(ValueError, match="Executable hash mismatch"):
        await transport.connect()
