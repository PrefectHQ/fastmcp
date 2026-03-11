"""Policy version manager with storage backend integration.

Manages policy version history with persistence, rollback, and diff
capabilities backed by a StorageBackend.

Example::

    from fastmcp.server.security.storage import SQLiteBackend
    from fastmcp.server.security.policy.versioning import PolicyVersionManager

    backend = SQLiteBackend("securemcp.db")
    manager = PolicyVersionManager(policy_set_id="production", backend=backend)

    # Create a version
    version = manager.create_version(
        policy_data={"roles": {"admin": ["*"]}},
        author="security-team",
        description="Initial RBAC setup",
    )

    # Rollback
    manager.rollback_to(version_number=1, reason="Reverting bad change")
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.security.policy.versioning.models import (
    PolicyVersion,
    PolicyVersionHistory,
    policy_version_from_dict,
    policy_version_to_dict,
)
from fastmcp.server.security.storage.backend import StorageBackend

logger = logging.getLogger(__name__)


class PolicyVersionManager:
    """Manages policy versioning with persistent storage.

    Attributes:
        policy_set_id: Identifier for the policy set being managed.
        backend: Storage backend for persistence.
    """

    def __init__(
        self,
        policy_set_id: str,
        backend: StorageBackend,
    ) -> None:
        self.policy_set_id = policy_set_id
        self._backend = backend
        self._history = PolicyVersionHistory(policy_set_id=policy_set_id)
        self._load_from_backend()

    def _load_from_backend(self) -> None:
        """Load version history from storage."""
        data = self._backend.load_policy_versions(self.policy_set_id)
        if data is None:
            return

        versions_data = data.get("versions", [])
        for vd in versions_data:
            version = policy_version_from_dict(vd)
            self._history.versions.append(version)

        self._history.current_version_index = data.get(
            "current_version_index",
            len(self._history.versions) - 1 if self._history.versions else -1,
        )

    def _save_to_backend(self) -> None:
        """Persist version history to storage."""
        data = {
            "versions": [policy_version_to_dict(v) for v in self._history.versions],
            "current_version_index": self._history.current_version_index,
        }
        self._backend.save_policy_version(self.policy_set_id, data)

    def create_version(
        self,
        policy_data: dict[str, Any],
        author: str = "",
        description: str = "",
        tags: frozenset[str] | None = None,
    ) -> PolicyVersion:
        """Create and persist a new version.

        Args:
            policy_data: Serialized policy configuration.
            author: Who created this version.
            description: Human-readable description.
            tags: Optional tags.

        Returns:
            The newly created PolicyVersion.
        """
        version = self._history.add_version(
            policy_data=policy_data,
            author=author,
            description=description,
            tags=tags,
        )
        self._save_to_backend()
        logger.info(
            "Created policy version %d for '%s' by %s",
            version.version_number,
            self.policy_set_id,
            author or "unknown",
        )
        return version

    @property
    def current_version(self) -> PolicyVersion | None:
        """Get the currently active version."""
        return self._history.current_version

    def rollback_to(self, version_number: int, reason: str = "") -> PolicyVersion:
        """Switch to a previous version.

        Args:
            version_number: Version number to activate (1-based).
            reason: Reason for rollback (logged).

        Returns:
            The activated PolicyVersion.

        Raises:
            ValueError: If version_number is invalid.
        """
        version = self._history.rollback(version_number)
        self._save_to_backend()
        logger.info(
            "Rolled back policy set '%s' to version %d: %s",
            self.policy_set_id,
            version_number,
            reason or "no reason given",
        )
        return version

    def list_versions(self) -> list[PolicyVersion]:
        """List all versions in chronological order."""
        return list(self._history.versions)

    def diff(self, v1_number: int, v2_number: int) -> dict[str, Any]:
        """Get differences between two versions.

        Args:
            v1_number: First version number (1-based).
            v2_number: Second version number (1-based).

        Returns:
            Dict with 'added', 'removed', 'changed' keys.
        """
        return self._history.diff(v1_number, v2_number)

    @property
    def version_count(self) -> int:
        """Number of versions in history."""
        return len(self._history.versions)
