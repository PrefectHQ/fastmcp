"""Authentication utility helpers."""

from __future__ import annotations

import json
from typing import Any


def parse_scopes(value: Any) -> list[str] | None:
    """Parse scopes from environment variables or settings values.

    Accepts either a JSON array string, a comma- or space-separated string,
    a list of strings, or ``None``. Returns a list of scopes or ``None`` if
    no value is provided.
    """
    if value is None or value == "":
        return None if value is None else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        # Try JSON array first
        if value.startswith("["):
            try:
                data = json.loads(value)
                if isinstance(data, list):
                    return [str(v).strip() for v in data if str(v).strip()]
            except Exception:
                pass
        # Fallback to comma/space separated list
        return [s.strip() for s in value.replace(",", " ").split() if s.strip()]
    return value
