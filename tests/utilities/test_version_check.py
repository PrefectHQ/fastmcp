"""Tests for version checking utilities."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from fastmcp.utilities.version_check import (
    CACHE_TTL_SECONDS,
    _fetch_latest_version,
    _get_cache_path,
    _read_cache,
    _write_cache,
    check_for_newer_version,
    get_latest_version,
)


class TestCachePath:
    def test_cache_path_in_home_directory(self):
        """Cache file should be in fastmcp home directory."""
        cache_path = _get_cache_path()
        assert cache_path.name == "version_cache.json"
        assert "fastmcp" in str(cache_path).lower()

    def test_cache_path_prerelease_suffix(self):
        """Prerelease cache uses different file."""
        cache_path = _get_cache_path(include_prereleases=True)
        assert cache_path.name == "version_cache_prerelease.json"


class TestReadCache:
    def test_read_cache_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Reading non-existent cache returns None."""
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: tmp_path / "nonexistent.json",
        )
        version, timestamp = _read_cache()
        assert version is None
        assert timestamp == 0

    def test_read_cache_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Reading valid cache returns version and timestamp."""
        cache_file = tmp_path / "version_cache.json"
        cache_file.write_text(
            json.dumps({"latest_version": "2.5.0", "timestamp": 1000})
        )
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        version, timestamp = _read_cache()
        assert version == "2.5.0"
        assert timestamp == 1000

    def test_read_cache_invalid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Reading invalid JSON returns None."""
        cache_file = tmp_path / "version_cache.json"
        cache_file.write_text("not valid json")
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        version, timestamp = _read_cache()
        assert version is None
        assert timestamp == 0


class TestWriteCache:
    def test_write_cache_creates_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Writing cache creates the cache file."""
        cache_file = tmp_path / "subdir" / "version_cache.json"
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        _write_cache("2.6.0")

        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["latest_version"] == "2.6.0"
        assert "timestamp" in data


class TestFetchLatestVersion:
    def test_fetch_success(self):
        """Successful fetch returns highest stable version."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "releases": {
                "2.5.0": [],
                "2.4.0": [],
                "2.6.0b1": [],  # prerelease should be skipped
            }
        }

        with patch("httpx.get", return_value=mock_response) as mock_get:
            version = _fetch_latest_version()
            assert version == "2.5.0"
            mock_get.assert_called_once()

    def test_fetch_network_error(self):
        """Network error returns None."""
        with patch("httpx.get", side_effect=httpx.HTTPError("Network error")):
            version = _fetch_latest_version()
            assert version is None

    def test_fetch_invalid_response(self):
        """Invalid response returns None."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "format"}

        with patch("httpx.get", return_value=mock_response):
            version = _fetch_latest_version()
            assert version is None

    def test_fetch_prereleases(self):
        """Fetching with prereleases returns highest version."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {"version": "2.5.0"},
            "releases": {
                "2.5.0": [],
                "2.6.0b1": [],
                "2.6.0b2": [],
                "2.4.0": [],
            },
        }

        with patch("httpx.get", return_value=mock_response):
            version = _fetch_latest_version(include_prereleases=True)
            assert version == "2.6.0b2"

    def test_fetch_prereleases_stable_is_highest(self):
        """Prerelease mode still returns stable if it's highest."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {"version": "2.5.0"},
            "releases": {
                "2.5.0": [],
                "2.5.0b1": [],
                "2.4.0": [],
            },
        }

        with patch("httpx.get", return_value=mock_response):
            version = _fetch_latest_version(include_prereleases=True)
            assert version == "2.5.0"


class TestGetLatestVersion:
    def test_returns_cached_version_if_fresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Uses cached version if cache is fresh."""
        cache_file = tmp_path / "version_cache.json"
        cache_file.write_text(
            json.dumps({"latest_version": "2.5.0", "timestamp": time.time()})
        )
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        with patch(
            "fastmcp.utilities.version_check._fetch_latest_version"
        ) as mock_fetch:
            version = get_latest_version()
            assert version == "2.5.0"
            mock_fetch.assert_not_called()

    def test_fetches_if_cache_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Fetches from PyPI if cache is stale."""
        cache_file = tmp_path / "version_cache.json"
        old_timestamp = time.time() - CACHE_TTL_SECONDS - 100
        cache_file.write_text(
            json.dumps({"latest_version": "2.4.0", "timestamp": old_timestamp})
        )
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        with patch(
            "fastmcp.utilities.version_check._fetch_latest_version",
            return_value="2.5.0",
        ) as mock_fetch:
            version = get_latest_version()
            assert version == "2.5.0"
            mock_fetch.assert_called_once()

    def test_returns_stale_cache_if_fetch_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Returns stale cache if fetch fails."""
        cache_file = tmp_path / "version_cache.json"
        old_timestamp = time.time() - CACHE_TTL_SECONDS - 100
        cache_file.write_text(
            json.dumps({"latest_version": "2.4.0", "timestamp": old_timestamp})
        )
        monkeypatch.setattr(
            "fastmcp.utilities.version_check._get_cache_path",
            lambda include_prereleases=False: cache_file,
        )

        with patch(
            "fastmcp.utilities.version_check._fetch_latest_version", return_value=None
        ):
            version = get_latest_version()
            assert version == "2.4.0"


class TestCheckForNewerVersion:
    def test_returns_none_if_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """Returns None if check_for_updates is off."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "off")

        result = check_for_newer_version()
        assert result is None

    def test_returns_none_if_current(self, monkeypatch: pytest.MonkeyPatch):
        """Returns None if current version is latest."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
        monkeypatch.setattr(fastmcp, "__version__", "2.5.0")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version", return_value="2.5.0"
        ):
            result = check_for_newer_version()
            assert result is None

    def test_returns_version_if_newer(self, monkeypatch: pytest.MonkeyPatch):
        """Returns new version if available."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
        monkeypatch.setattr(fastmcp, "__version__", "2.4.0")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version", return_value="2.5.0"
        ):
            result = check_for_newer_version()
            assert result == "2.5.0"

    def test_returns_none_if_older_available(self, monkeypatch: pytest.MonkeyPatch):
        """Returns None if pypi version is older than current (dev version)."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
        monkeypatch.setattr(fastmcp, "__version__", "3.0.0.dev1")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version", return_value="2.5.0"
        ):
            result = check_for_newer_version()
            assert result is None

    def test_handles_invalid_versions(self, monkeypatch: pytest.MonkeyPatch):
        """Handles invalid version strings gracefully."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
        monkeypatch.setattr(fastmcp, "__version__", "invalid")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version",
            return_value="also-invalid",
        ):
            result = check_for_newer_version()
            assert result is None

    def test_prerelease_setting(self, monkeypatch: pytest.MonkeyPatch):
        """Prerelease setting passes include_prereleases=True."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "prerelease")
        monkeypatch.setattr(fastmcp, "__version__", "2.5.0")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version", return_value="2.6.0b1"
        ) as mock_get:
            result = check_for_newer_version()
            assert result == "2.6.0b1"
            mock_get.assert_called_once_with(True)

    def test_stable_setting(self, monkeypatch: pytest.MonkeyPatch):
        """Stable setting passes include_prereleases=False."""
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
        monkeypatch.setattr(fastmcp, "__version__", "2.4.0")

        with patch(
            "fastmcp.utilities.version_check.get_latest_version", return_value="2.5.0"
        ) as mock_get:
            result = check_for_newer_version()
            assert result == "2.5.0"
            mock_get.assert_called_once_with(False)
