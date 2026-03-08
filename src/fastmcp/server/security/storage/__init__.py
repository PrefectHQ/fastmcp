"""Pluggable storage backends for SecureMCP.

Provides a ``StorageBackend`` protocol and two implementations:

- ``MemoryBackend``: In-memory (default, matches existing behavior).
- ``SQLiteBackend``: Single-file SQLite persistence.
"""

from fastmcp.server.security.storage.backend import StorageBackend
from fastmcp.server.security.storage.memory import MemoryBackend
from fastmcp.server.security.storage.sqlite import SQLiteBackend

__all__ = [
    "MemoryBackend",
    "SQLiteBackend",
    "StorageBackend",
]
