"""Tool Marketplace — publish, discover, and install certified MCP tools.

Extends the server-level Marketplace with tool-level granularity:
publishers submit tool packages with security manifests, tools are
certified through the CertificationPipeline, and consumers discover
tools via rich search (categories, trust scores, reviews, popularity).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
    ToolAttestation,
)
from fastmcp.server.security.certification.manifest import SecurityManifest

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus
    from fastmcp.server.security.registry.registry import TrustRegistry

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    """Categories for marketplace tool classification."""

    DATA_ACCESS = "data_access"
    FILE_SYSTEM = "file_system"
    NETWORK = "network"
    CODE_EXECUTION = "code_execution"
    AI_ML = "ai_ml"
    COMMUNICATION = "communication"
    SEARCH = "search"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    MONITORING = "monitoring"
    UTILITY = "utility"
    OTHER = "other"


class PublishStatus(Enum):
    """Status of a tool listing in the marketplace."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class ReviewRating(Enum):
    """Star ratings for tool reviews."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


@dataclass
class ToolReview:
    """A user review of a marketplace tool.

    Attributes:
        review_id: Unique identifier.
        tool_listing_id: The listing being reviewed.
        reviewer_id: Who submitted the review.
        rating: 1-5 star rating.
        title: Short review title.
        body: Full review text.
        verified_user: Whether the reviewer is a verified tool user.
        created_at: When the review was posted.
    """

    review_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_listing_id: str = ""
    reviewer_id: str = ""
    rating: ReviewRating = ReviewRating.THREE
    title: str = ""
    body: str = ""
    verified_user: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "review_id": self.review_id,
            "tool_listing_id": self.tool_listing_id,
            "reviewer_id": self.reviewer_id,
            "rating": self.rating.value,
            "title": self.title,
            "body": self.body,
            "verified_user": self.verified_user,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class InstallRecord:
    """Record of a tool installation.

    Attributes:
        install_id: Unique identifier.
        tool_listing_id: The listing installed.
        installer_id: Who installed it.
        version: Version installed.
        installed_at: When installed.
        uninstalled_at: When uninstalled (if applicable).
        active: Whether the install is currently active.
    """

    install_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_listing_id: str = ""
    installer_id: str = ""
    version: str = ""
    installed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    uninstalled_at: datetime | None = None
    active: bool = True


@dataclass
class ToolListing:
    """A tool's listing in the marketplace.

    Combines the tool's identity, security manifest, certification
    status, reviews, and install statistics into a single discoverable
    record.

    Attributes:
        listing_id: Unique listing identifier.
        tool_name: MCP tool name.
        display_name: Human-friendly name for display.
        description: Tool description (markdown supported).
        version: Current published version.
        author: Publisher identity.
        categories: Classification tags.
        manifest: Security manifest (if provided).
        attestation: Current certification attestation.
        status: Publishing status.
        reviews: User reviews.
        install_count: Total installs.
        active_installs: Currently active installs.
        created_at: When first published.
        updated_at: Last modification time.
        homepage_url: Project homepage.
        source_url: Source code URL.
        license: License identifier (SPDX).
        tags: Searchable keywords.
        metadata: Additional listing data.
    """

    listing_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = ""
    author: str = ""
    categories: set[ToolCategory] = field(default_factory=set)
    manifest: SecurityManifest | None = None
    attestation: ToolAttestation | None = None
    status: PublishStatus = PublishStatus.DRAFT
    reviews: list[ToolReview] = field(default_factory=list)
    install_count: int = 0
    active_installs: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    homepage_url: str = ""
    source_url: str = ""
    license: str = ""
    tags: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def certification_level(self) -> CertificationLevel:
        """Current certification level from attestation."""
        if self.attestation is not None and self.attestation.is_valid():
            return self.attestation.certification_level
        return CertificationLevel.UNCERTIFIED

    @property
    def is_certified(self) -> bool:
        """Whether the tool has a valid certification."""
        return self.attestation is not None and self.attestation.is_valid()

    @property
    def average_rating(self) -> float:
        """Average review rating (0.0 if no reviews)."""
        if not self.reviews:
            return 0.0
        return sum(r.rating.value for r in self.reviews) / len(self.reviews)

    @property
    def review_count(self) -> int:
        """Number of reviews."""
        return len(self.reviews)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "listing_id": self.listing_id,
            "tool_name": self.tool_name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "categories": [c.value for c in self.categories],
            "certification_level": self.certification_level.value,
            "is_certified": self.is_certified,
            "status": self.status.value,
            "average_rating": round(self.average_rating, 2),
            "review_count": self.review_count,
            "install_count": self.install_count,
            "active_installs": self.active_installs,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "homepage_url": self.homepage_url,
            "source_url": self.source_url,
            "license": self.license,
            "tags": sorted(self.tags),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact summary for search results."""
        return {
            "listing_id": self.listing_id,
            "tool_name": self.tool_name,
            "display_name": self.display_name,
            "author": self.author,
            "version": self.version,
            "certification_level": self.certification_level.value,
            "average_rating": round(self.average_rating, 2),
            "install_count": self.install_count,
            "categories": [c.value for c in self.categories],
        }


class SortBy(Enum):
    """Sorting options for marketplace search."""

    RELEVANCE = "relevance"
    TRUST_SCORE = "trust_score"
    RATING = "rating"
    INSTALLS = "installs"
    NEWEST = "newest"
    RECENTLY_UPDATED = "recently_updated"


class ToolMarketplace:
    """Tool-level marketplace for publishing, discovering, and installing tools.

    Integrates with the TrustRegistry for trust scores and the
    CertificationPipeline for attestation. Supports rich search,
    user reviews, and install tracking.

    Example::

        marketplace = ToolMarketplace(trust_registry=registry)

        # Publish a tool
        listing = marketplace.publish(
            tool_name="search-docs",
            display_name="Document Search",
            author="acme",
            version="1.0.0",
            categories={ToolCategory.SEARCH, ToolCategory.DATA_ACCESS},
            manifest=manifest,
            attestation=attestation,
        )

        # Search for tools
        results = marketplace.search(
            query="search",
            categories={ToolCategory.SEARCH},
            min_certification=CertificationLevel.BASIC,
        )

        # Install a tool
        record = marketplace.install(listing.listing_id, installer_id="user-1")

    Args:
        trust_registry: Optional TrustRegistry for trust score lookups.
        event_bus: Optional event bus for marketplace events.
    """

    def __init__(
        self,
        *,
        trust_registry: TrustRegistry | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self._trust_registry = trust_registry
        self._event_bus = event_bus
        self._listings: dict[str, ToolListing] = {}  # keyed by listing_id
        self._name_index: dict[str, str] = {}  # tool_name → listing_id
        self._installs: dict[str, list[InstallRecord]] = {}  # listing_id → installs

    def publish(
        self,
        tool_name: str,
        *,
        display_name: str = "",
        description: str = "",
        version: str = "",
        author: str = "",
        categories: set[ToolCategory] | None = None,
        manifest: SecurityManifest | None = None,
        attestation: ToolAttestation | None = None,
        status: PublishStatus = PublishStatus.PUBLISHED,
        homepage_url: str = "",
        source_url: str = "",
        tool_license: str = "",
        tags: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolListing:
        """Publish a tool to the marketplace.

        If a listing already exists for this tool_name, it is updated
        (version bump). Otherwise a new listing is created.

        Args:
            tool_name: MCP tool name (unique identifier).
            display_name: Human-friendly name.
            description: Tool description.
            version: Version string.
            author: Publisher identity.
            categories: Tool categories.
            manifest: Security manifest.
            attestation: Certification attestation.
            status: Initial publish status.
            homepage_url: Project homepage.
            source_url: Source code URL.
            tool_license: SPDX license identifier.
            tags: Searchable keywords.
            metadata: Additional data.

        Returns:
            The created or updated ToolListing.
        """
        existing_id = self._name_index.get(tool_name)

        if existing_id is not None:
            listing = self._listings[existing_id]
            listing.display_name = display_name or listing.display_name
            listing.description = description or listing.description
            listing.version = version or listing.version
            listing.author = author or listing.author
            if categories:
                listing.categories = categories
            if manifest is not None:
                listing.manifest = manifest
            if attestation is not None:
                listing.attestation = attestation
            listing.status = status
            listing.homepage_url = homepage_url or listing.homepage_url
            listing.source_url = source_url or listing.source_url
            listing.license = tool_license or listing.license
            if tags:
                listing.tags.update(tags)
            if metadata:
                listing.metadata.update(metadata)
            listing.updated_at = datetime.now(timezone.utc)

            self._emit_event("TOOL_UPDATED", listing)
            logger.info("Tool listing updated: %s (v%s)", tool_name, version)
            return listing

        listing = ToolListing(
            tool_name=tool_name,
            display_name=display_name or tool_name,
            description=description,
            version=version,
            author=author,
            categories=categories or set(),
            manifest=manifest,
            attestation=attestation,
            status=status,
            homepage_url=homepage_url,
            source_url=source_url,
            license=tool_license,
            tags=tags or set(),
            metadata=metadata or {},
        )

        self._listings[listing.listing_id] = listing
        self._name_index[tool_name] = listing.listing_id
        self._installs[listing.listing_id] = []

        # Also register in the trust registry if available
        if self._trust_registry is not None:
            self._trust_registry.register(
                tool_name,
                tool_version=version,
                author=author,
                attestation=attestation,
                tags=tags,
            )

        self._emit_event("TOOL_PUBLISHED", listing)
        logger.info("Tool published: %s (v%s)", tool_name, version)
        return listing

    def unpublish(self, listing_id: str) -> bool:
        """Remove a tool from the marketplace.

        Returns True if the listing was found and removed.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return False

        del self._listings[listing_id]
        self._name_index.pop(listing.tool_name, None)
        self._installs.pop(listing_id, None)

        if self._trust_registry is not None:
            self._trust_registry.unregister(listing.tool_name)

        self._emit_event("TOOL_UNPUBLISHED", listing)
        return True

    def get(self, listing_id: str) -> ToolListing | None:
        """Get a listing by ID."""
        return self._listings.get(listing_id)

    def get_by_name(self, tool_name: str) -> ToolListing | None:
        """Get a listing by tool name."""
        listing_id = self._name_index.get(tool_name)
        if listing_id is None:
            return None
        return self._listings.get(listing_id)

    def search(
        self,
        *,
        query: str | None = None,
        categories: set[ToolCategory] | None = None,
        min_certification: CertificationLevel | None = None,
        certified_only: bool = False,
        author: str | None = None,
        tags: set[str] | None = None,
        min_rating: float | None = None,
        min_installs: int | None = None,
        status: PublishStatus | None = None,
        sort_by: SortBy = SortBy.RELEVANCE,
        limit: int = 50,
    ) -> list[ToolListing]:
        """Search for tools in the marketplace.

        All filters are AND-combined. Omitted filters match everything.

        Args:
            query: Free-text search (matches name, description, tags).
            categories: Filter by category (any match).
            min_certification: Minimum certification level.
            certified_only: Only return certified tools.
            author: Filter by author.
            tags: Required tags (any match).
            min_rating: Minimum average rating.
            min_installs: Minimum install count.
            status: Filter by publish status (defaults to PUBLISHED).
            sort_by: Sort order for results.
            limit: Maximum results.

        Returns:
            Matching listings sorted by the specified criteria.
        """
        level_order = list(CertificationLevel)
        effective_status = status if status is not None else PublishStatus.PUBLISHED
        results: list[ToolListing] = []

        for listing in self._listings.values():
            # Status filter
            if listing.status != effective_status:
                continue

            # Text search
            if query is not None:
                q_lower = query.lower()
                searchable = (
                    f"{listing.tool_name} {listing.display_name} "
                    f"{listing.description} {' '.join(listing.tags)}"
                ).lower()
                if q_lower not in searchable:
                    continue

            # Category filter (any match)
            if categories and not categories.intersection(listing.categories):
                continue

            # Certification filters
            if certified_only and not listing.is_certified:
                continue
            if min_certification is not None:
                if level_order.index(listing.certification_level) < level_order.index(
                    min_certification
                ):
                    continue

            # Author filter
            if author is not None and listing.author != author:
                continue

            # Tags filter (any match)
            if tags and not tags.intersection(listing.tags):
                continue

            # Rating filter
            if min_rating is not None and listing.average_rating < min_rating:
                continue

            # Installs filter
            if min_installs is not None and listing.install_count < min_installs:
                continue

            results.append(listing)

        # Sort
        results = self._sort_results(results, sort_by)

        return results[:limit]

    def add_review(
        self,
        listing_id: str,
        *,
        reviewer_id: str,
        rating: ReviewRating,
        title: str = "",
        body: str = "",
        verified_user: bool = False,
    ) -> ToolReview | None:
        """Add a review to a tool listing.

        Returns the review if the listing was found, None otherwise.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return None

        review = ToolReview(
            tool_listing_id=listing_id,
            reviewer_id=reviewer_id,
            rating=rating,
            title=title,
            body=body,
            verified_user=verified_user,
        )
        listing.reviews.append(review)
        listing.updated_at = datetime.now(timezone.utc)

        # Feed into trust registry reputation
        if self._trust_registry is not None:
            from fastmcp.server.security.registry.reputation import ReputationTracker

            tracker = ReputationTracker(registry=self._trust_registry)
            tracker.report_review(
                listing.tool_name,
                positive=rating.value >= 4,
                description=f"Review by {reviewer_id}: {rating.value}/5",
            )

        return review

    def get_reviews(
        self, listing_id: str, *, limit: int = 50
    ) -> list[ToolReview]:
        """Get reviews for a listing."""
        listing = self._listings.get(listing_id)
        if listing is None:
            return []
        # Most recent first
        reviews = sorted(listing.reviews, key=lambda r: r.created_at, reverse=True)
        return reviews[:limit]

    def install(
        self,
        listing_id: str,
        *,
        installer_id: str = "",
        version: str | None = None,
    ) -> InstallRecord | None:
        """Record a tool installation.

        Returns the install record if the listing was found, None otherwise.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return None

        record = InstallRecord(
            tool_listing_id=listing_id,
            installer_id=installer_id,
            version=version or listing.version,
        )

        if listing_id not in self._installs:
            self._installs[listing_id] = []
        self._installs[listing_id].append(record)

        listing.install_count += 1
        listing.active_installs += 1

        # Report successful installation to trust registry
        if self._trust_registry is not None:
            from fastmcp.server.security.registry.reputation import ReputationTracker

            tracker = ReputationTracker(registry=self._trust_registry)
            tracker.report_success(listing.tool_name, actor_id=installer_id)

        return record

    def uninstall(
        self,
        listing_id: str,
        *,
        installer_id: str = "",
    ) -> bool:
        """Record a tool uninstallation.

        Returns True if an active install was found and deactivated.
        """
        installs = self._installs.get(listing_id, [])

        for record in reversed(installs):
            if record.active and (not installer_id or record.installer_id == installer_id):
                record.active = False
                record.uninstalled_at = datetime.now(timezone.utc)
                listing = self._listings.get(listing_id)
                if listing is not None:
                    listing.active_installs = max(0, listing.active_installs - 1)
                return True

        return False

    def get_installs(
        self, listing_id: str, *, active_only: bool = False
    ) -> list[InstallRecord]:
        """Get install records for a listing."""
        installs = self._installs.get(listing_id, [])
        if active_only:
            return [r for r in installs if r.active]
        return list(installs)

    def update_status(
        self, listing_id: str, status: PublishStatus
    ) -> bool:
        """Update a listing's publish status.

        Returns True if the listing was found.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return False
        listing.status = status
        listing.updated_at = datetime.now(timezone.utc)
        return True

    def update_attestation(
        self, listing_id: str, attestation: ToolAttestation
    ) -> bool:
        """Update a listing's certification attestation.

        Also syncs to the trust registry.

        Returns True if the listing was found.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return False

        listing.attestation = attestation
        listing.updated_at = datetime.now(timezone.utc)

        if self._trust_registry is not None:
            self._trust_registry.update_attestation(listing.tool_name, attestation)

        return True

    def get_featured(self, *, limit: int = 10) -> list[ToolListing]:
        """Get featured/trending tools.

        Returns published tools sorted by a composite of trust score,
        recent installs, and rating.
        """
        return self.search(
            sort_by=SortBy.TRUST_SCORE,
            certified_only=True,
            limit=limit,
        )

    def get_by_author(self, author: str) -> list[ToolListing]:
        """Get all listings by an author."""
        return [
            listing
            for listing in self._listings.values()
            if listing.author == author
        ]

    def get_by_category(
        self, category: ToolCategory, *, limit: int = 50
    ) -> list[ToolListing]:
        """Get published tools in a category."""
        return self.search(categories={category}, limit=limit)

    @property
    def listing_count(self) -> int:
        """Total listings in the marketplace."""
        return len(self._listings)

    @property
    def published_count(self) -> int:
        """Number of published listings."""
        return sum(
            1 for l in self._listings.values()
            if l.status == PublishStatus.PUBLISHED
        )

    def get_all_listings(self) -> list[ToolListing]:
        """Get all listings regardless of status."""
        return list(self._listings.values())

    def get_statistics(self) -> dict[str, Any]:
        """Get marketplace statistics."""
        total = len(self._listings)
        published = sum(1 for l in self._listings.values() if l.status == PublishStatus.PUBLISHED)
        certified = sum(1 for l in self._listings.values() if l.is_certified)
        total_installs = sum(l.install_count for l in self._listings.values())
        total_reviews = sum(l.review_count for l in self._listings.values())

        # Category distribution
        category_counts: dict[str, int] = {}
        for listing in self._listings.values():
            for cat in listing.categories:
                category_counts[cat.value] = category_counts.get(cat.value, 0) + 1

        return {
            "total_listings": total,
            "published_listings": published,
            "certified_tools": certified,
            "total_installs": total_installs,
            "total_reviews": total_reviews,
            "categories": category_counts,
        }

    # ── Sorting ───────────────────────────────────────────────────────

    def _sort_results(
        self, results: list[ToolListing], sort_by: SortBy
    ) -> list[ToolListing]:
        """Sort search results."""
        if sort_by == SortBy.TRUST_SCORE:
            return sorted(
                results,
                key=lambda l: self._get_trust_score(l.tool_name),
                reverse=True,
            )
        elif sort_by == SortBy.RATING:
            return sorted(results, key=lambda l: l.average_rating, reverse=True)
        elif sort_by == SortBy.INSTALLS:
            return sorted(results, key=lambda l: l.install_count, reverse=True)
        elif sort_by == SortBy.NEWEST:
            return sorted(results, key=lambda l: l.created_at, reverse=True)
        elif sort_by == SortBy.RECENTLY_UPDATED:
            return sorted(results, key=lambda l: l.updated_at, reverse=True)
        else:
            # RELEVANCE: composite of trust + rating + installs
            return sorted(
                results,
                key=lambda l: (
                    self._get_trust_score(l.tool_name) * 0.4
                    + (l.average_rating / 5.0) * 0.3
                    + min(l.install_count / 1000.0, 1.0) * 0.3
                ),
                reverse=True,
            )

    def _get_trust_score(self, tool_name: str) -> float:
        """Get trust score from the registry, or 0.0 if unavailable."""
        if self._trust_registry is None:
            return 0.0
        score = self._trust_registry.get_trust_score(tool_name)
        if score is None:
            return 0.0
        return score.overall

    # ── Event emission ────────────────────────────────────────────────

    def _emit_event(self, action: str, listing: ToolListing) -> None:
        """Emit a marketplace event."""
        if self._event_bus is None:
            return

        from fastmcp.server.security.alerts.models import (
            AlertSeverity,
            SecurityEvent,
            SecurityEventType,
        )

        event_map = {
            "TOOL_PUBLISHED": SecurityEventType.SERVER_REGISTERED,
            "TOOL_UPDATED": SecurityEventType.TRUST_CHANGED,
            "TOOL_UNPUBLISHED": SecurityEventType.SERVER_UNREGISTERED,
        }

        self._event_bus.emit(
            SecurityEvent(
                event_type=event_map.get(action, SecurityEventType.SERVER_REGISTERED),
                severity=AlertSeverity.INFO,
                layer="tool_marketplace",
                message=f"Tool marketplace: {action} — {listing.tool_name} v{listing.version}",
                resource_id=listing.listing_id,
                data={
                    "action": action,
                    "tool_name": listing.tool_name,
                    "author": listing.author,
                    "version": listing.version,
                },
            )
        )
