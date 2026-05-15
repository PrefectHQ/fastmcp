"""Version checking utilities for FastMCP."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from packaging.version import Version

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

PYPI_URL = "https://pypi.org/pypi/fastmcp/json"
CACHE_TTL_SECONDS = 60 * 60 * 12  # 12 hours
REQUEST_TIMEOUT_SECONDS = 2.0


def _get_cache_path(include_prereleases: bool = False) -> Path:
    """Get the path to the version cache file."""
    import fastmcp

    suffix = "_prerelease" if include_prereleases else ""
    return fastmcp.settings.home / f"version_cache{suffix}.json"


def _read_cache(include_prereleases: bool = False) -> tuple[str | None, float]:
    """Read cached version info.

    Returns:
        Tuple of (cached_version, cache_timestamp) or (None, 0) if no cache.
    """
    cache_path = _get_cache_path(include_prereleases)
    if not cache_path.exists():
        return None, 0

    try:
        data = json.loads(cache_path.read_text())
        return data.get("latest_version"), data.get("timestamp", 0)
    except (json.JSONDecodeError, OSError):
        return None, 0


def _write_cache(latest_version: str, include_prereleases: bool = False) -> None:
    """Write version info to cache."""
    cache_path = _get_cache_path(include_prereleases)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"latest_version": latest_version, "timestamp": time.time()})
        )
    except OSError:
        # Silently ignore cache write failures
        pass


def _fetch_latest_version(include_prereleases: bool = False) -> str | None:
    """Fetch the latest version from PyPI.

    Args:
        include_prereleases: If True, include pre-release versions (alpha, beta, rc).

    Returns:
        The latest version string, or None if the fetch failed.
    """
    try:
        response = httpx.get(PYPI_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        releases = data.get("releases", {})
        if not releases:
            return None

        versions = []
        for version_str in releases:
            try:
                v = Version(version_str)
                # Skip prereleases if not requested
                if not include_prereleases and v.is_prerelease:
                    continue
                versions.append(v)
            except ValueError:
                logger.debug(f"Skipping invalid version string: {version_str}")
                continue

        if not versions:
            return None

        return str(max(versions))

    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return None


def get_latest_version(include_prereleases: bool = False) -> str | None:
    """Get the latest version of FastMCP from PyPI, using cache when available.

    Args:
        include_prereleases: If True, include pre-release versions.

    Returns:
        The latest version string, or None if unavailable.
    """
    # Check cache first
    cached_version, cache_timestamp = _read_cache(include_prereleases)
    if cached_version and (time.time() - cache_timestamp) < CACHE_TTL_SECONDS:
        return cached_version

    # Fetch from PyPI
    latest_version = _fetch_latest_version(include_prereleases)

    # Update cache if we got a valid version
    if latest_version:
        _write_cache(latest_version, include_prereleases)
        return latest_version

    # Return stale cache if available
    return cached_version


def check_for_newer_version() -> str | None:
    """Check if a newer version of FastMCP is available.

    Returns:
        The latest version string if newer than current, None otherwise.
    """
    import fastmcp

    setting = fastmcp.settings.check_for_updates
    if setting == "off":
        return None

    include_prereleases = setting == "prerelease"
    latest_version = get_latest_version(include_prereleases)
    if not latest_version:
        return None

    try:
        current = Version(fastmcp.__version__)
        latest = Version(latest_version)

        if latest > current:
            return latest_version
    except ValueError:
        logger.debug(
            f"Could not compare versions: current={fastmcp.__version__!r}, "
            f"latest={latest_version!r}"
        )

    return None
