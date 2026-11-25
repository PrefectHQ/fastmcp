from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=repository, text=True
    ).strip()


def same_json(stored: object, value: object) -> bool:
    """Whether `value` equals `stored` once both are serialized as JSON.

    Anything read back from the Prefect API (artifact data, run parameters) has
    been through JSON, so an int dict key or a tuple in the in-memory value would
    never compare equal to its own round-trip.
    """

    def canonical(item: object) -> str:
        return json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)

    return canonical(stored) == canonical(value)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
