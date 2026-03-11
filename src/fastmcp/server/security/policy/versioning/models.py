"""Policy versioning data models.

Immutable snapshots of policy configurations with metadata for audit trails.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PolicyVersion:
    """A snapshot of a policy configuration at a point in time.

    Attributes:
        version_id: Unique identifier for this version.
        policy_set_id: Identifier of the policy set being versioned.
        version_number: Monotonically increasing version number.
        policy_data: Serialized representation of the policies.
        created_at: When this version was created.
        author: Who created this version.
        description: Human-readable description of changes.
        tags: Arbitrary tags for categorization.
    """

    version_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    policy_set_id: str = ""
    version_number: int = 0
    policy_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    author: str = ""
    description: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)


def policy_version_to_dict(version: PolicyVersion) -> dict[str, Any]:
    """Serialize a PolicyVersion to a JSON-safe dict."""
    return {
        "version_id": version.version_id,
        "policy_set_id": version.policy_set_id,
        "version_number": version.version_number,
        "policy_data": version.policy_data,
        "created_at": version.created_at.isoformat(),
        "author": version.author,
        "description": version.description,
        "tags": sorted(version.tags),
    }


def policy_version_from_dict(data: dict[str, Any]) -> PolicyVersion:
    """Deserialize a PolicyVersion from a dict."""
    return PolicyVersion(
        version_id=data["version_id"],
        policy_set_id=data["policy_set_id"],
        version_number=data["version_number"],
        policy_data=data.get("policy_data", {}),
        created_at=datetime.fromisoformat(data["created_at"]),
        author=data.get("author", ""),
        description=data.get("description", ""),
        tags=frozenset(data.get("tags", [])),
    )


@dataclass
class PolicyVersionHistory:
    """Tracks the full version history of a policy set.

    Attributes:
        policy_set_id: Identifier for this policy set.
        versions: Ordered list of all versions (oldest first).
        current_version_index: Index of the currently active version.
    """

    policy_set_id: str = ""
    versions: list[PolicyVersion] = field(default_factory=list)
    current_version_index: int = -1

    @property
    def current_version(self) -> PolicyVersion | None:
        """Get the currently active version."""
        if not self.versions or self.current_version_index < 0:
            return None
        if self.current_version_index >= len(self.versions):
            return None
        return self.versions[self.current_version_index]

    def add_version(
        self,
        policy_data: dict[str, Any],
        author: str = "",
        description: str = "",
        tags: frozenset[str] | None = None,
    ) -> PolicyVersion:
        """Create and record a new version.

        The new version becomes the current version automatically.

        Returns:
            The newly created PolicyVersion.
        """
        version_number = len(self.versions) + 1
        version = PolicyVersion(
            policy_set_id=self.policy_set_id,
            version_number=version_number,
            policy_data=policy_data,
            author=author,
            description=description,
            tags=tags or frozenset(),
        )
        self.versions.append(version)
        self.current_version_index = len(self.versions) - 1
        return version

    def rollback(self, version_number: int) -> PolicyVersion:
        """Switch the active version to a previous one.

        Args:
            version_number: The version number to activate (1-based).

        Returns:
            The activated PolicyVersion.

        Raises:
            ValueError: If the version number is invalid.
        """
        index = version_number - 1
        if index < 0 or index >= len(self.versions):
            raise ValueError(
                f"Invalid version number {version_number}. "
                f"Valid range: 1-{len(self.versions)}"
            )
        self.current_version_index = index
        return self.versions[index]

    def diff(self, v1_number: int, v2_number: int) -> dict[str, Any]:
        """Compare two versions and return their differences.

        Args:
            v1_number: First version number (1-based).
            v2_number: Second version number (1-based).

        Returns:
            Dict with keys 'added', 'removed', 'changed' showing differences
            in policy_data between the two versions.

        Raises:
            ValueError: If either version number is invalid.
        """
        for num in (v1_number, v2_number):
            idx = num - 1
            if idx < 0 or idx >= len(self.versions):
                raise ValueError(
                    f"Invalid version number {num}. Valid range: 1-{len(self.versions)}"
                )

        v1_data = self.versions[v1_number - 1].policy_data
        v2_data = self.versions[v2_number - 1].policy_data

        v1_keys = set(v1_data.keys())
        v2_keys = set(v2_data.keys())

        return {
            "added": {k: v2_data[k] for k in v2_keys - v1_keys},
            "removed": {k: v1_data[k] for k in v1_keys - v2_keys},
            "changed": {
                k: {"from": v1_data[k], "to": v2_data[k]}
                for k in v1_keys & v2_keys
                if v1_data[k] != v2_data[k]
            },
        }
