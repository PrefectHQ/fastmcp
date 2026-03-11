"""PureCipher product-layer data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PublisherSummary:
    """Summary of a publisher's public footprint in the registry."""

    publisher_id: str
    display_name: str
    listing_count: int
    average_trust: float | None
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    latest_activity: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON responses."""

        return {
            "publisher_id": self.publisher_id,
            "display_name": self.display_name,
            "listing_count": self.listing_count,
            "average_trust": self.average_trust,
            "categories": list(self.categories),
            "tags": list(self.tags),
            "latest_activity": self.latest_activity,
        }


@dataclass(frozen=True)
class PublisherProfile:
    """Public publisher profile backed by published listings."""

    summary: PublisherSummary
    listings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON responses."""

        payload = self.summary.to_dict()
        payload["listings"] = list(self.listings)
        return payload


@dataclass(frozen=True)
class ReviewQueueItem:
    """Moderation queue view of a single listing."""

    listing_id: str
    tool_name: str
    display_name: str
    author: str
    publisher_id: str
    status: str
    certification_level: str
    trust_score: float | None
    version: str
    updated_at: str
    available_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON responses."""

        return {
            "listing_id": self.listing_id,
            "tool_name": self.tool_name,
            "display_name": self.display_name,
            "author": self.author,
            "publisher_id": self.publisher_id,
            "status": self.status,
            "certification_level": self.certification_level,
            "trust_score": self.trust_score,
            "version": self.version,
            "updated_at": self.updated_at,
            "available_actions": list(self.available_actions),
        }


__all__ = ["PublisherProfile", "PublisherSummary", "ReviewQueueItem"]
