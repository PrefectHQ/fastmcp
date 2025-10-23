"""Key management utilities for FastMCP.

Provides automatic key generation and storage in system keyring for
Mac/Windows platforms, with graceful fallback for Linux/headless systems.
"""

from __future__ import annotations

import base64
import platform
import secrets

import keyring

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


def get_or_generate_keyring_key(key_type: str, namespace: str) -> str | None:
    """Get or generate a key from the system keyring.

    Keys are namespaced to allow multiple isolated key sets.

    Args:
        key_type: Type of key (e.g., "jwt-signing", "token-encryption", "api-key")
        namespace: Unique identifier for this key set (e.g., client ID, server name)

    Returns:
        Base64-encoded key string, or None if keyring unavailable

    Example:
        >>> key = get_or_generate_keyring_key("jwt-signing", "my-github-client-id")
        >>> # Returns key from keyring or generates new one
    """
    # Linux keyring support is unreliable (GUI sessions, unlock prompts, backend issues)
    if platform.system() == "Linux":
        return None

    service_name = "fastmcp"
    # Namespace keys for isolation
    key_name = f"{key_type}-{namespace}"

    try:
        # Try to get existing key from keyring
        existing_key = keyring.get_password(service_name, key_name)
        if existing_key:
            logger.debug(
                "Retrieved %s for namespace=%s from system keyring",
                key_type,
                namespace,
            )
            return existing_key

        # Generate new secure random key (32 bytes for Fernet/HMAC)
        key_bytes = secrets.token_bytes(32)
        key_b64 = base64.b64encode(key_bytes).decode()

        # Store in keyring for future use
        keyring.set_password(service_name, key_name, key_b64)
        logger.info(
            "Generated new %s for namespace=%s and stored in system keyring",
            key_type,
            namespace,
        )
        return key_b64

    except Exception as e:
        # Keyring backend may not be available (headless systems, permissions, etc.)
        logger.warning(
            "Failed to access system keyring for %s: %s. "
            "Will use ephemeral key (tokens will not survive restart).",
            key_type,
            e,
        )
        return None
