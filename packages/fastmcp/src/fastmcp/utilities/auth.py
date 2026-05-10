"""Authentication utility helpers."""

from __future__ import annotations

import base64
import json
from typing import Any


def _decode_jwt_part(token: str, part_index: int) -> dict[str, Any]:
    """Decode a JWT part (header or payload) without signature verification.

    Args:
        token: JWT token string (header.payload.signature)
        part_index: 0 for header, 1 for payload

    Returns:
        Decoded part as a dictionary

    Raises:
        ValueError: If token is not a valid JWT format
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format (expected 3 parts)")

    part_b64 = parts[part_index]
    part_b64 += "=" * (-len(part_b64) % 4)  # Add padding
    return json.loads(base64.urlsafe_b64decode(part_b64))


def decode_jwt_header(token: str) -> dict[str, Any]:
    """Decode JWT header without signature verification.

    Useful for extracting the key ID (kid) for JWKS lookup.

    Args:
        token: JWT token string (header.payload.signature)

    Returns:
        Decoded header as a dictionary

    Raises:
        ValueError: If token is not a valid JWT format
    """
    return _decode_jwt_part(token, 0)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification.

    Use only for tokens received directly from trusted sources (e.g., IdP token endpoints).

    Args:
        token: JWT token string (header.payload.signature)

    Returns:
        Decoded payload as a dictionary

    Raises:
        ValueError: If token is not a valid JWT format
    """
    return _decode_jwt_part(token, 1)


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
