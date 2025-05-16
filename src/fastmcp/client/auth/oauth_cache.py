import json
import time
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

from fastmcp.client.auth.httpx_client import logger
from fastmcp.settings import settings


class OAuthCache:
    """Manages OAuth credentials and tokens caching."""

    # Class variables
    CACHE_DIR: ClassVar[Path] = settings.home / "oauth-cache"

    def __init__(self):
        """Initialize the cache directory."""
        self.CACHE_DIR.mkdir(exist_ok=True, parents=True)

    @staticmethod
    def get_base_url(url: str) -> str:
        """Extract the base URL (scheme + host) from a URL with a path."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_cache_key(self, url: str) -> str:
        """Generate a safe filesystem key from a URL, automatically extracting the base URL."""
        base_url = self.get_base_url(url)
        # Replace scheme:// and non-alphanumeric characters with _ for safety
        return base_url.replace("://", "_").replace(".", "_").replace("/", "_")

    def get_file_path(self, url: str, file_type: Literal["client", "token"]) -> Path:
        """Get the file path for the specified cache file type and URL."""
        key = self.get_cache_key(url)
        return self.CACHE_DIR / f"{key}_{file_type}.json"

    def save(
        self, url: str, data: dict[str, Any], file_type: Literal["client", "token"]
    ) -> None:
        """Save data to the cache file using the base URL extracted from url."""
        path = self.get_file_path(url, file_type)
        path.write_text(json.dumps(data))
        base_url = self.get_base_url(url)
        logger.debug(f"Saved {file_type} data for {base_url}")

    def load(
        self, url: str, file_type: Literal["client", "token"]
    ) -> dict[str, Any] | None:
        """Load data from the cache file using the base URL extracted from url."""
        path = self.get_file_path(url, file_type)
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            base_url = self.get_base_url(url)
            logger.debug(f"No valid {file_type} cache found for {base_url}")
            return None

    def has_valid_token(self, url: str) -> bool:
        """Check if there's a valid non-expired token for the given URL."""
        token = self.load(url, "token")
        if not token:
            return False

        # Check expiration
        expires_at = token.get("expires_at")
        if not expires_at or expires_at < time.time():
            return False

        return True

    def list_cached_endpoints(self) -> list[str]:
        """List all base URLs with cached credentials or tokens."""
        endpoints = set()

        file_types = ["client", "token"]
        for file_type in file_types:
            for file in self.CACHE_DIR.glob(f"*_{file_type}.json"):
                key = file.name.replace(f"_{file_type}.json", "")
                # This is a simplified conversion back to URL format
                # May need enhancement for complex URLs
                url = key.replace("_", "://", 1)
                # Attempt to reconstruct the URL in a basic way
                parts = url.split("_")
                if len(parts) > 1:
                    # Reconstruct with dots and slashes
                    reconstructed = parts[0]
                    for part in parts[1:]:
                        if part:
                            reconstructed += f".{part}"
                    endpoints.add(reconstructed)
                else:
                    endpoints.add(url)

        return sorted(list(endpoints))

    def clear(self, url: str | None = None) -> None:
        """
        Clear the OAuth cache for a specific URL or all cached data.

        Args:
            url: The URL to clear cache for. If None, clears all cache.
        """
        if url is None:
            # Clear all files in the cache directory
            file_types = ["client", "token"]
            for file_type in file_types:
                for file in self.CACHE_DIR.glob(f"*_{file_type}.json"):
                    file.unlink(missing_ok=True)
            logger.info("Cleared all OAuth cache data")
        else:
            # Clear only files for the specific URL
            path = self.get_file_path(url, "client")
            path.unlink(missing_ok=True)
            path = self.get_file_path(url, "token")
            path.unlink(missing_ok=True)
            base_url = self.get_base_url(url)
            logger.info(f"Cleared OAuth cache for {base_url}")


# Initialize global cache instance
oauth_cache = OAuthCache()
