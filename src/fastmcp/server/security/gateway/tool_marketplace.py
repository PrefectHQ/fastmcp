"""Tool Marketplace — publish, discover, and install certified MCP tools.

Extends the server-level Marketplace with tool-level granularity:
publishers submit tool packages with security manifests, tools are
certified through the CertificationPipeline, and consumers discover
tools via rich search (categories, trust scores, reviews, popularity).

Includes persistence, versioning, moderation workflow, and signature
verification on install.
"""

from __future__ import annotations

import hashlib
import json
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
    from fastmcp.server.security.storage.backend import StorageBackend

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


class ModerationAction(Enum):
    """Actions a moderator can take on a listing."""

    APPROVE = "approve"
    REJECT = "reject"
    SUSPEND = "suspend"
    UNSUSPEND = "unsuspend"
    DEPRECATE = "deprecate"
    REQUEST_CHANGES = "request_changes"


@dataclass
class ModerationDecision:
    """A moderation decision on a tool listing.

    Attributes:
        decision_id: Unique identifier.
        listing_id: The listing being moderated.
        moderator_id: Who made the decision.
        action: The moderation action taken.
        reason: Explanation for the decision.
        created_at: When the decision was made.
        metadata: Additional context.
    """

    decision_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    listing_id: str = ""
    moderator_id: str = ""
    action: ModerationAction = ModerationAction.APPROVE
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "decision_id": self.decision_id,
            "listing_id": self.listing_id,
            "moderator_id": self.moderator_id,
            "action": self.action.value,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class ToolVersion:
    """A versioned snapshot of a tool listing.

    Attributes:
        version: Semantic version string.
        manifest_digest: SHA-256 of the manifest at this version.
        attestation_id: Certification attestation at release time.
        changelog: What changed in this version.
        published_at: When this version was released.
        yanked: Whether this version has been pulled.
        yank_reason: Why the version was yanked.
    """

    version: str = ""
    manifest_digest: str = ""
    attestation_id: str = ""
    changelog: str = ""
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    yanked: bool = False
    yank_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "version": self.version,
            "manifest_digest": self.manifest_digest,
            "attestation_id": self.attestation_id,
            "changelog": self.changelog,
            "published_at": self.published_at.isoformat(),
            "yanked": self.yanked,
            "yank_reason": self.yank_reason,
        }


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
        signature_verified: Whether the package signature was verified.
    """

    install_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_listing_id: str = ""
    installer_id: str = ""
    version: str = ""
    installed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    uninstalled_at: datetime | None = None
    active: bool = True
    signature_verified: bool = False


@dataclass
class ToolListing:
    """A tool's listing in the marketplace.

    Combines the tool's identity, security manifest, certification
    status, reviews, version history, and install statistics into a
    single discoverable record.

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
        version_history: All published versions.
        moderation_log: Moderation decisions.
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
    version_history: list[ToolVersion] = field(default_factory=list)
    moderation_log: list[ModerationDecision] = field(default_factory=list)
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

    @property
    def latest_version(self) -> ToolVersion | None:
        """Most recently published non-yanked version."""
        for v in reversed(self.version_history):
            if not v.yanked:
                return v
        return None

    @property
    def available_versions(self) -> list[str]:
        """List of non-yanked version strings."""
        return [v.version for v in self.version_history if not v.yanked]

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
            "version_count": len(self.version_history),
            "available_versions": self.available_versions,
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


# ── Signature verification helpers ────────────────────────────


def compute_manifest_digest(manifest: SecurityManifest) -> str:
    """Compute a SHA-256 digest of a manifest for integrity verification."""
    payload = json.dumps(manifest.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_attestation_signature(
    attestation: ToolAttestation,
    manifest: SecurityManifest | None = None,
) -> tuple[bool, str]:
    """Verify an attestation's integrity.

    Checks:
    1. Attestation is currently valid (not expired/revoked).
    2. If a manifest is provided, its digest matches the attestation.
    3. The attestation has a non-empty signature.

    Returns:
        (passed, reason) tuple.
    """
    if not attestation.is_valid():
        return False, f"Attestation status is {attestation.status.value}, not valid"

    if not attestation.signature:
        return False, "Attestation has no signature"

    if manifest is not None and attestation.manifest_digest:
        expected_digest = compute_manifest_digest(manifest)
        if attestation.manifest_digest != expected_digest:
            return False, (
                f"Manifest digest mismatch: attestation expects "
                f"{attestation.manifest_digest[:16]}..., "
                f"got {expected_digest[:16]}..."
            )

    return True, "Signature verification passed"


# ── Main marketplace class ────────────────────────────────────


class ToolMarketplace:
    """Tool-level marketplace for publishing, discovering, and installing tools.

    Integrates with the TrustRegistry for trust scores, the
    CertificationPipeline for attestation, and an optional
    StorageBackend for persistence. Supports rich search,
    user reviews, install tracking, version history, moderation,
    and signature verification.

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

        # Install a tool (with signature verification)
        record = marketplace.install(
            listing.listing_id,
            installer_id="user-1",
            verify_signature=True,
        )

    Args:
        trust_registry: Optional TrustRegistry for trust score lookups.
        event_bus: Optional event bus for marketplace events.
        backend: Optional storage backend for persistence.
        marketplace_id: Identifier for this marketplace instance.
        require_moderation: If True, new listings start as PENDING_REVIEW.
    """

    def __init__(
        self,
        *,
        trust_registry: TrustRegistry | None = None,
        event_bus: SecurityEventBus | None = None,
        backend: StorageBackend | None = None,
        marketplace_id: str = "default",
        require_moderation: bool = False,
    ) -> None:
        self._trust_registry = trust_registry
        self._event_bus = event_bus
        self._backend = backend
        self._marketplace_id = marketplace_id
        self._require_moderation = require_moderation
        self._listings: dict[str, ToolListing] = {}  # keyed by listing_id
        self._name_index: dict[str, str] = {}  # tool_name → listing_id
        self._installs: dict[str, list[InstallRecord]] = {}  # listing_id → installs

        if self._backend is not None:
            self._load_from_backend()

    def _load_from_backend(self) -> None:
        """Load marketplace state from persistence."""
        if self._backend is None:
            return
        try:
            data = self._backend.load_tool_marketplace(self._marketplace_id)
            for listing_id, listing_data in data.get("listings", {}).items():
                listing = self._deserialize_listing(listing_data)
                self._listings[listing_id] = listing
                self._name_index[listing.tool_name] = listing_id
            logger.debug(
                "Loaded %d tool listings from backend", len(self._listings)
            )
        except Exception:
            logger.debug("Failed to load tool marketplace from backend", exc_info=True)

    def _persist_listing(self, listing: ToolListing) -> None:
        """Persist a listing to the backend."""
        if self._backend is None:
            return
        try:
            self._backend.save_tool_listing(
                self._marketplace_id,
                listing.listing_id,
                listing.to_dict(),
            )
        except Exception:
            logger.debug("Failed to persist listing %s", listing.listing_id, exc_info=True)

    def _remove_persisted_listing(self, listing_id: str) -> None:
        """Remove a listing from the backend."""
        if self._backend is None:
            return
        try:
            self._backend.remove_tool_listing(self._marketplace_id, listing_id)
        except Exception:
            logger.debug("Failed to remove listing %s", listing_id, exc_info=True)

    @staticmethod
    def _deserialize_listing(data: dict[str, Any]) -> ToolListing:
        """Reconstruct a ToolListing from a persisted dict."""
        listing = ToolListing(
            listing_id=data.get("listing_id", str(uuid.uuid4())),
            tool_name=data.get("tool_name", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            version=data.get("version", ""),
            author=data.get("author", ""),
            status=PublishStatus(data.get("status", "draft")),
            install_count=data.get("install_count", 0),
            active_installs=data.get("active_installs", 0),
            homepage_url=data.get("homepage_url", ""),
            source_url=data.get("source_url", ""),
            license=data.get("license", ""),
        )
        # Restore categories
        for cat_val in data.get("categories", []):
            try:
                listing.categories.add(ToolCategory(cat_val))
            except ValueError:
                pass
        # Restore tags
        for tag in data.get("tags", []):
            listing.tags.add(tag)
        return listing

    # ── Publishing ────────────────────────────────────────────

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
        changelog: str = "",
    ) -> ToolListing:
        """Publish a tool to the marketplace.

        If a listing already exists for this tool_name, it is updated
        (version bump). Otherwise a new listing is created.

        When ``require_moderation`` is True, new listings start as
        PENDING_REVIEW regardless of the ``status`` parameter.

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
            changelog: What changed in this version.

        Returns:
            The created or updated ToolListing.
        """
        existing_id = self._name_index.get(tool_name)

        if existing_id is not None:
            listing = self._listings[existing_id]
            old_version = listing.version
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

            # Record version history if version changed
            if version and version != old_version:
                manifest_digest = ""
                if manifest is not None:
                    manifest_digest = compute_manifest_digest(manifest)
                attestation_id = ""
                if attestation is not None:
                    attestation_id = attestation.attestation_id
                tv = ToolVersion(
                    version=version,
                    manifest_digest=manifest_digest,
                    attestation_id=attestation_id,
                    changelog=changelog,
                )
                listing.version_history.append(tv)

            self._persist_listing(listing)
            self._emit_event("TOOL_UPDATED", listing)
            logger.info("Tool listing updated: %s (v%s)", tool_name, version)
            return listing

        # Determine initial status
        effective_status = status
        if self._require_moderation and status == PublishStatus.PUBLISHED:
            effective_status = PublishStatus.PENDING_REVIEW

        listing = ToolListing(
            tool_name=tool_name,
            display_name=display_name or tool_name,
            description=description,
            version=version,
            author=author,
            categories=categories or set(),
            manifest=manifest,
            attestation=attestation,
            status=effective_status,
            homepage_url=homepage_url,
            source_url=source_url,
            license=tool_license,
            tags=tags or set(),
            metadata=metadata or {},
        )

        # Record initial version
        if version:
            manifest_digest = ""
            if manifest is not None:
                manifest_digest = compute_manifest_digest(manifest)
            attestation_id = ""
            if attestation is not None:
                attestation_id = attestation.attestation_id
            tv = ToolVersion(
                version=version,
                manifest_digest=manifest_digest,
                attestation_id=attestation_id,
                changelog=changelog or "Initial release",
            )
            listing.version_history.append(tv)

        self._listings[listing.listing_id] = listing
        self._name_index[tool_name] = listing.listing_id
        self._installs[listing.listing_id] = []

        # Register in the trust registry if available
        if self._trust_registry is not None:
            self._trust_registry.register(
                tool_name,
                tool_version=version,
                author=author,
                attestation=attestation,
                tags=tags,
            )

        self._persist_listing(listing)
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

        self._remove_persisted_listing(listing_id)
        self._emit_event("TOOL_UNPUBLISHED", listing)
        return True

    # ── Lookup ────────────────────────────────────────────────

    def get(self, listing_id: str) -> ToolListing | None:
        """Get a listing by ID."""
        return self._listings.get(listing_id)

    def get_by_name(self, tool_name: str) -> ToolListing | None:
        """Get a listing by tool name."""
        listing_id = self._name_index.get(tool_name)
        if listing_id is None:
            return None
        return self._listings.get(listing_id)

    # ── Search ────────────────────────────────────────────────

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
            if listing.status != effective_status:
                continue

            if query is not None:
                q_lower = query.lower()
                searchable = (
                    f"{listing.tool_name} {listing.display_name} "
                    f"{listing.description} {' '.join(listing.tags)}"
                ).lower()
                if q_lower not in searchable:
                    continue

            if categories and not categories.intersection(listing.categories):
                continue

            if certified_only and not listing.is_certified:
                continue
            if min_certification is not None:
                if level_order.index(listing.certification_level) < level_order.index(
                    min_certification
                ):
                    continue

            if author is not None and listing.author != author:
                continue

            if tags and not tags.intersection(listing.tags):
                continue

            if min_rating is not None and listing.average_rating < min_rating:
                continue

            if min_installs is not None and listing.install_count < min_installs:
                continue

            results.append(listing)

        results = self._sort_results(results, sort_by)
        return results[:limit]

    # ── Reviews ───────────────────────────────────────────────

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

        # Persist review
        if self._backend is not None:
            try:
                self._backend.append_tool_review(
                    self._marketplace_id, listing_id, review.to_dict()
                )
            except Exception:
                logger.debug("Failed to persist review", exc_info=True)

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
        reviews = sorted(listing.reviews, key=lambda r: r.created_at, reverse=True)
        return reviews[:limit]

    # ── Installation (with signature verification) ────────────

    def install(
        self,
        listing_id: str,
        *,
        installer_id: str = "",
        version: str | None = None,
        verify_signature: bool = False,
    ) -> InstallRecord | None:
        """Record a tool installation.

        When ``verify_signature`` is True, the attestation's integrity
        is checked before allowing the install. If verification fails,
        None is returned.

        Args:
            listing_id: The listing to install.
            installer_id: Who is installing.
            version: Specific version to install (defaults to latest).
            verify_signature: Whether to verify the attestation signature.

        Returns:
            The install record, or None if the listing was not found
            or signature verification failed.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return None

        signature_verified = False

        if verify_signature:
            if listing.attestation is None:
                logger.warning(
                    "Install rejected: no attestation for %s", listing.tool_name
                )
                self._emit_event("INSTALL_REJECTED", listing)
                return None

            passed, reason = verify_attestation_signature(
                listing.attestation, listing.manifest
            )
            if not passed:
                logger.warning(
                    "Install rejected for %s: %s", listing.tool_name, reason
                )
                self._emit_event("INSTALL_REJECTED", listing)
                return None
            signature_verified = True

        # Resolve version
        install_version = version or listing.version
        if version:
            # Check version exists and is not yanked
            version_record = self.get_version(listing_id, version)
            if version_record is not None and version_record.yanked:
                logger.warning(
                    "Install rejected: version %s is yanked for %s",
                    version,
                    listing.tool_name,
                )
                return None

        record = InstallRecord(
            tool_listing_id=listing_id,
            installer_id=installer_id,
            version=install_version,
            signature_verified=signature_verified,
        )

        if listing_id not in self._installs:
            self._installs[listing_id] = []
        self._installs[listing_id].append(record)

        listing.install_count += 1
        listing.active_installs += 1

        # Persist install
        if self._backend is not None:
            try:
                self._backend.append_tool_install(
                    self._marketplace_id,
                    listing_id,
                    {
                        "install_id": record.install_id,
                        "installer_id": record.installer_id,
                        "version": record.version,
                        "installed_at": record.installed_at.isoformat(),
                        "signature_verified": record.signature_verified,
                    },
                )
            except Exception:
                logger.debug("Failed to persist install record", exc_info=True)

        # Report successful installation to trust registry
        if self._trust_registry is not None:
            from fastmcp.server.security.registry.reputation import ReputationTracker

            tracker = ReputationTracker(registry=self._trust_registry)
            tracker.report_success(listing.tool_name, actor_id=installer_id)

        self._emit_event("TOOL_INSTALLED", listing)
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

    # ── Version management ────────────────────────────────────

    def get_version_history(self, listing_id: str) -> list[ToolVersion]:
        """Get the version history for a listing."""
        listing = self._listings.get(listing_id)
        if listing is None:
            return []
        return list(listing.version_history)

    def get_version(self, listing_id: str, version: str) -> ToolVersion | None:
        """Get a specific version record."""
        listing = self._listings.get(listing_id)
        if listing is None:
            return None
        for v in listing.version_history:
            if v.version == version:
                return v
        return None

    def yank_version(
        self,
        listing_id: str,
        version: str,
        *,
        reason: str = "",
    ) -> bool:
        """Yank (pull) a specific version, preventing new installs.

        Returns True if the version was found and yanked.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return False

        for v in listing.version_history:
            if v.version == version:
                v.yanked = True
                v.yank_reason = reason
                listing.updated_at = datetime.now(timezone.utc)
                self._persist_listing(listing)
                logger.info(
                    "Version %s yanked for %s: %s",
                    version,
                    listing.tool_name,
                    reason,
                )
                return True
        return False

    def unyank_version(self, listing_id: str, version: str) -> bool:
        """Restore a yanked version.

        Returns True if the version was found and restored.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return False

        for v in listing.version_history:
            if v.version == version:
                v.yanked = False
                v.yank_reason = ""
                listing.updated_at = datetime.now(timezone.utc)
                self._persist_listing(listing)
                return True
        return False

    # ── Moderation ────────────────────────────────────────────

    @property
    def require_moderation(self) -> bool:
        """Whether new listings require moderation before publishing."""
        return self._require_moderation

    def get_pending_review(self) -> list[ToolListing]:
        """Get all listings awaiting moderation."""
        return [
            listing
            for listing in self._listings.values()
            if listing.status == PublishStatus.PENDING_REVIEW
        ]

    def moderate(
        self,
        listing_id: str,
        *,
        moderator_id: str,
        action: ModerationAction,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ModerationDecision | None:
        """Apply a moderation decision to a listing.

        Transitions the listing status based on the action:
          - APPROVE → PUBLISHED
          - REJECT → REJECTED
          - SUSPEND → SUSPENDED
          - UNSUSPEND → PUBLISHED
          - DEPRECATE → DEPRECATED
          - REQUEST_CHANGES → DRAFT

        Returns the decision if the listing was found, None otherwise.
        """
        listing = self._listings.get(listing_id)
        if listing is None:
            return None

        decision = ModerationDecision(
            listing_id=listing_id,
            moderator_id=moderator_id,
            action=action,
            reason=reason,
            metadata=metadata or {},
        )

        # Status transitions
        status_map: dict[ModerationAction, PublishStatus] = {
            ModerationAction.APPROVE: PublishStatus.PUBLISHED,
            ModerationAction.REJECT: PublishStatus.REJECTED,
            ModerationAction.SUSPEND: PublishStatus.SUSPENDED,
            ModerationAction.UNSUSPEND: PublishStatus.PUBLISHED,
            ModerationAction.DEPRECATE: PublishStatus.DEPRECATED,
            ModerationAction.REQUEST_CHANGES: PublishStatus.DRAFT,
        }

        new_status = status_map.get(action)
        if new_status is not None:
            listing.status = new_status

        listing.moderation_log.append(decision)
        listing.updated_at = datetime.now(timezone.utc)

        self._persist_listing(listing)
        self._emit_event(f"TOOL_MODERATED_{action.value.upper()}", listing)
        logger.info(
            "Tool %s moderated: %s by %s — %s",
            listing.tool_name,
            action.value,
            moderator_id,
            reason,
        )
        return decision

    def get_moderation_log(self, listing_id: str) -> list[ModerationDecision]:
        """Get the moderation history for a listing."""
        listing = self._listings.get(listing_id)
        if listing is None:
            return []
        return list(listing.moderation_log)

    # ── Status management ─────────────────────────────────────

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
        self._persist_listing(listing)
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

        self._persist_listing(listing)
        return True

    # ── Discovery helpers ─────────────────────────────────────

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
        pending = sum(1 for l in self._listings.values() if l.status == PublishStatus.PENDING_REVIEW)
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
            "pending_review": pending,
            "total_installs": total_installs,
            "total_reviews": total_reviews,
            "categories": category_counts,
        }

    # ── Sorting ───────────────────────────────────────────────

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

    # ── Event emission ────────────────────────────────────────

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
            "TOOL_INSTALLED": SecurityEventType.SERVER_REGISTERED,
            "INSTALL_REJECTED": SecurityEventType.TRUST_CHANGED,
        }

        # For moderation events, use TRUST_CHANGED as a sensible default
        event_type = event_map.get(action, SecurityEventType.TRUST_CHANGED)

        severity = AlertSeverity.INFO
        if "REJECTED" in action or "SUSPEND" in action:
            severity = AlertSeverity.WARNING

        self._event_bus.emit(
            SecurityEvent(
                event_type=event_type,
                severity=severity,
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
