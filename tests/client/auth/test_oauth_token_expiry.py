"""Test OAuth token expiry handling with absolute timestamps."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthToken

from fastmcp.client.auth.oauth import FileTokenStorage


@pytest.mark.asyncio
async def test_token_storage_with_expiry(tmp_path: Path):
    """Test that tokens are stored with absolute expiry time and loaded correctly."""
    storage = FileTokenStorage("http://test.example.com", cache_dir=tmp_path)

    # Create a token with 3600 seconds expiry
    token = OAuthToken(
        access_token="test_token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="refresh_token",
    )

    # Save the token
    await storage.set_tokens(token)

    # Check that the file contains the dataclass format
    # JSONFileStorage wraps data in {"data": ..., "timestamp": ...}
    token_file = storage._get_file_path("tokens")
    wrapper = json.loads(token_file.read_text())

    assert "data" in wrapper
    assert "timestamp" in wrapper
    data = wrapper["data"]

    assert "token_payload" in data
    assert "expires_at" in data
    assert data["expires_at"] is not None
    # expires_at should be approximately now + 3600 seconds
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(seconds=3600)
    assert abs((expires_at - expected).total_seconds()) < 2

    # Load the token back
    loaded_token = await storage.get_tokens()
    assert loaded_token is not None
    assert loaded_token.access_token == "test_token"
    # expires_in should be recalculated to be approximately 3600 (minus loading time)
    assert loaded_token.expires_in is not None
    assert 3595 <= loaded_token.expires_in <= 3600


@pytest.mark.asyncio
async def test_expired_token_returns_none(tmp_path: Path):
    """Test that expired tokens return None when loaded."""
    storage = FileTokenStorage("http://test.example.com", cache_dir=tmp_path)

    # Manually create an already-expired token file
    token_file = storage._get_file_path("tokens")
    past_expiry = datetime.now(timezone.utc) - timedelta(
        seconds=10
    )  # Expired 10 seconds ago

    expired_token = {
        "token_payload": {
            "access_token": "test_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "refresh_token",
        },
        "expires_at": past_expiry.isoformat(),
    }
    token_file.write_text(json.dumps(expired_token, indent=2, default=str))

    # Load the token - should return None since it's expired
    loaded_token = await storage.get_tokens()
    assert loaded_token is None


@pytest.mark.asyncio
async def test_token_without_expiry(tmp_path: Path):
    """Test that tokens without expires_in are handled correctly."""
    storage = FileTokenStorage("http://test.example.com", cache_dir=tmp_path)

    # Create a token without expires_in (perpetual token)
    token = OAuthToken(
        access_token="test_token",
        token_type="Bearer",
        expires_in=None,
        refresh_token="refresh_token",
    )

    # Save the token
    await storage.set_tokens(token)

    # Check that expires_at is None in the file
    # JSONFileStorage wraps data in {"data": ..., "timestamp": ...}
    token_file = storage._get_file_path("tokens")
    wrapper = json.loads(token_file.read_text())
    data = wrapper["data"]
    assert data["expires_at"] is None

    # Load the token back - should work since no expiry
    loaded_token = await storage.get_tokens()
    assert loaded_token is not None
    assert loaded_token.access_token == "test_token"
    assert loaded_token.expires_in is None


@pytest.mark.asyncio
async def test_invalid_format_returns_none(tmp_path: Path):
    """Test that invalid token format returns None."""
    storage = FileTokenStorage("http://test.example.com", cache_dir=tmp_path)

    # Manually write an invalid format token file (missing required fields)
    token_file = storage._get_file_path("tokens")
    invalid_token = {
        "access_token": "invalid_token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "refresh_token",
    }
    token_file.write_text(json.dumps(invalid_token, indent=2))

    # Try to load - should return None
    loaded_token = await storage.get_tokens()
    assert loaded_token is None


@pytest.mark.asyncio
async def test_token_expiry_recalculated_on_load(tmp_path: Path):
    """Test that expires_in is correctly recalculated when loading tokens."""
    storage = FileTokenStorage("http://test.example.com", cache_dir=tmp_path)

    # Manually create a token file with a specific expires_at
    token_file = storage._get_file_path("tokens")
    future_expiry = datetime.now(timezone.utc) + timedelta(
        seconds=1800
    )  # 30 minutes from now

    # JSONFileStorage expects wrapped format
    stored_token = {
        "data": {
            "token_payload": {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,  # Original value (will be recalculated)
                "refresh_token": "refresh_token",
            },
            "expires_at": future_expiry.isoformat(),
        },
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }
    token_file.write_text(json.dumps(stored_token, indent=2, default=str))

    # Load the token
    loaded_token = await storage.get_tokens()
    assert loaded_token is not None
    # expires_in should be recalculated to approximately 1800 seconds
    assert loaded_token.expires_in is not None
    assert 1795 <= loaded_token.expires_in <= 1800
