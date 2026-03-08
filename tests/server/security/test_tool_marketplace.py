"""Tests for the Tool Marketplace (Phase 14).

Covers tool publishing, discovery, reviews, install tracking,
trust registry integration, and event bus integration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.alerts.handlers import BufferedHandler
from fastmcp.server.security.certification.attestation import (
    AttestationStatus,
    CertificationLevel,
    ToolAttestation,
)
from fastmcp.server.security.certification.manifest import (
    SecurityManifest,
)
from fastmcp.server.security.gateway.tool_marketplace import (
    InstallRecord,
    PublishStatus,
    ReviewRating,
    SortBy,
    ToolCategory,
    ToolListing,
    ToolMarketplace,
    ToolReview,
)
from fastmcp.server.security.registry.registry import TrustRegistry


# ── Helpers ─────────────────────────────────────────────────────────


def _make_attestation(
    level: CertificationLevel = CertificationLevel.STANDARD,
    *,
    valid: bool = True,
) -> ToolAttestation:
    """Create a test attestation."""
    att = ToolAttestation(
        certification_level=level,
        status=AttestationStatus.VALID if valid else AttestationStatus.EXPIRED,
    )
    if valid:
        att.expires_at = datetime.now(timezone.utc) + timedelta(days=365)
    else:
        att.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    return att


def _make_manifest(tool_name: str = "test-tool") -> SecurityManifest:
    """Create a test manifest."""
    return SecurityManifest(
        tool_name=tool_name,
        version="1.0.0",
        author="test-author",
    )


def _make_marketplace(**kwargs) -> ToolMarketplace:
    """Create a test marketplace."""
    return ToolMarketplace(**kwargs)


def _publish_tool(
    marketplace: ToolMarketplace,
    name: str = "test-tool",
    *,
    author: str = "acme",
    version: str = "1.0.0",
    categories: set[ToolCategory] | None = None,
    attestation: ToolAttestation | None = None,
    tags: set[str] | None = None,
) -> ToolListing:
    """Publish a test tool."""
    return marketplace.publish(
        name,
        display_name=name.replace("-", " ").title(),
        description=f"A test tool called {name}",
        version=version,
        author=author,
        categories=categories or {ToolCategory.UTILITY},
        attestation=attestation,
        tags=tags or {name},
    )


# ── ToolListing model tests ────────────────────────────────────────


class TestToolListing:
    """Tests for the ToolListing dataclass."""

    def test_default_listing(self):
        listing = ToolListing()
        assert listing.listing_id
        assert listing.tool_name == ""
        assert listing.status == PublishStatus.DRAFT
        assert listing.certification_level == CertificationLevel.UNCERTIFIED
        assert not listing.is_certified
        assert listing.average_rating == 0.0
        assert listing.review_count == 0

    def test_certified_listing(self):
        att = _make_attestation(CertificationLevel.STANDARD)
        listing = ToolListing(attestation=att)
        assert listing.is_certified
        assert listing.certification_level == CertificationLevel.STANDARD

    def test_expired_attestation(self):
        att = _make_attestation(CertificationLevel.STANDARD, valid=False)
        listing = ToolListing(attestation=att)
        assert not listing.is_certified
        assert listing.certification_level == CertificationLevel.UNCERTIFIED

    def test_average_rating(self):
        listing = ToolListing()
        listing.reviews = [
            ToolReview(rating=ReviewRating.FIVE),
            ToolReview(rating=ReviewRating.THREE),
            ToolReview(rating=ReviewRating.FOUR),
        ]
        assert listing.average_rating == pytest.approx(4.0)
        assert listing.review_count == 3

    def test_to_dict(self):
        listing = ToolListing(
            tool_name="test-tool",
            display_name="Test Tool",
            author="acme",
            categories={ToolCategory.SEARCH},
            tags={"search", "docs"},
        )
        d = listing.to_dict()
        assert d["tool_name"] == "test-tool"
        assert d["author"] == "acme"
        assert "search" in d["categories"]
        assert d["is_certified"] is False

    def test_to_summary_dict(self):
        listing = ToolListing(
            tool_name="test-tool",
            display_name="Test Tool",
            author="acme",
        )
        s = listing.to_summary_dict()
        assert s["tool_name"] == "test-tool"
        assert "description" not in s  # summary is compact


# ── ToolReview model tests ──────────────────────────────────────────


class TestToolReview:
    """Tests for the ToolReview dataclass."""

    def test_default_review(self):
        review = ToolReview()
        assert review.review_id
        assert review.rating == ReviewRating.THREE
        assert not review.verified_user

    def test_review_to_dict(self):
        review = ToolReview(
            reviewer_id="user-1",
            rating=ReviewRating.FIVE,
            title="Great tool!",
            body="Works perfectly.",
            verified_user=True,
        )
        d = review.to_dict()
        assert d["rating"] == 5
        assert d["verified_user"] is True
        assert d["title"] == "Great tool!"


# ── Publishing tests ────────────────────────────────────────────────


class TestToolPublishing:
    """Tests for publishing tools to the marketplace."""

    def test_publish_new_tool(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "search-docs")
        assert listing.tool_name == "search-docs"
        assert listing.status == PublishStatus.PUBLISHED
        assert mp.listing_count == 1
        assert mp.published_count == 1

    def test_publish_updates_existing(self):
        mp = _make_marketplace()
        listing1 = _publish_tool(mp, "search-docs", version="1.0.0")
        listing2 = _publish_tool(mp, "search-docs", version="2.0.0")
        assert listing1.listing_id == listing2.listing_id
        assert listing2.version == "2.0.0"
        assert mp.listing_count == 1

    def test_publish_with_attestation(self):
        mp = _make_marketplace()
        att = _make_attestation(CertificationLevel.STANDARD)
        listing = _publish_tool(mp, "secure-tool", attestation=att)
        assert listing.is_certified
        assert listing.certification_level == CertificationLevel.STANDARD

    def test_publish_with_manifest(self):
        mp = _make_marketplace()
        manifest = _make_manifest("my-tool")
        listing = mp.publish(
            "my-tool",
            manifest=manifest,
            version="1.0.0",
        )
        assert listing.manifest is not None
        assert listing.manifest.tool_name == "my-tool"

    def test_publish_multiple_tools(self):
        mp = _make_marketplace()
        _publish_tool(mp, "tool-a")
        _publish_tool(mp, "tool-b")
        _publish_tool(mp, "tool-c")
        assert mp.listing_count == 3

    def test_unpublish(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "temp-tool")
        assert mp.unpublish(listing.listing_id)
        assert mp.listing_count == 0
        assert mp.get(listing.listing_id) is None

    def test_unpublish_nonexistent(self):
        mp = _make_marketplace()
        assert not mp.unpublish("nonexistent-id")

    def test_update_status(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        mp.update_status(listing.listing_id, PublishStatus.DEPRECATED)
        assert listing.status == PublishStatus.DEPRECATED

    def test_update_attestation(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        assert not listing.is_certified
        att = _make_attestation(CertificationLevel.BASIC)
        mp.update_attestation(listing.listing_id, att)
        assert listing.is_certified


# ── Lookup tests ────────────────────────────────────────────────────


class TestToolLookup:
    """Tests for looking up tools."""

    def test_get_by_id(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        assert mp.get(listing.listing_id) is listing

    def test_get_by_name(self):
        mp = _make_marketplace()
        _publish_tool(mp, "my-tool")
        result = mp.get_by_name("my-tool")
        assert result is not None
        assert result.tool_name == "my-tool"

    def test_get_nonexistent(self):
        mp = _make_marketplace()
        assert mp.get("nonexistent") is None
        assert mp.get_by_name("nonexistent") is None

    def test_get_by_author(self):
        mp = _make_marketplace()
        _publish_tool(mp, "tool-a", author="alice")
        _publish_tool(mp, "tool-b", author="alice")
        _publish_tool(mp, "tool-c", author="bob")
        alice_tools = mp.get_by_author("alice")
        assert len(alice_tools) == 2

    def test_get_by_category(self):
        mp = _make_marketplace()
        _publish_tool(mp, "search-a", categories={ToolCategory.SEARCH})
        _publish_tool(mp, "search-b", categories={ToolCategory.SEARCH})
        _publish_tool(mp, "db-tool", categories={ToolCategory.DATABASE})
        results = mp.get_by_category(ToolCategory.SEARCH)
        assert len(results) == 2

    def test_get_all_listings(self):
        mp = _make_marketplace()
        _publish_tool(mp, "a")
        _publish_tool(mp, "b")
        assert len(mp.get_all_listings()) == 2


# ── Search tests ────────────────────────────────────────────────────


class TestToolSearch:
    """Tests for marketplace search."""

    def test_search_by_query(self):
        mp = _make_marketplace()
        _publish_tool(mp, "search-docs")
        _publish_tool(mp, "file-writer")
        results = mp.search(query="search")
        assert len(results) == 1
        assert results[0].tool_name == "search-docs"

    def test_search_by_category(self):
        mp = _make_marketplace()
        _publish_tool(mp, "tool-a", categories={ToolCategory.SEARCH})
        _publish_tool(mp, "tool-b", categories={ToolCategory.DATABASE})
        results = mp.search(categories={ToolCategory.SEARCH})
        assert len(results) == 1

    def test_search_by_author(self):
        mp = _make_marketplace()
        _publish_tool(mp, "a", author="alice")
        _publish_tool(mp, "b", author="bob")
        results = mp.search(author="alice")
        assert len(results) == 1

    def test_search_by_tags(self):
        mp = _make_marketplace()
        _publish_tool(mp, "tool-a", tags={"search", "ml"})
        _publish_tool(mp, "tool-b", tags={"database"})
        results = mp.search(tags={"ml"})
        assert len(results) == 1

    def test_search_certified_only(self):
        mp = _make_marketplace()
        att = _make_attestation(CertificationLevel.STANDARD)
        _publish_tool(mp, "certified-tool", attestation=att)
        _publish_tool(mp, "uncertified-tool")
        results = mp.search(certified_only=True)
        assert len(results) == 1
        assert results[0].tool_name == "certified-tool"

    def test_search_min_certification(self):
        mp = _make_marketplace()
        att_basic = _make_attestation(CertificationLevel.BASIC)
        att_standard = _make_attestation(CertificationLevel.STANDARD)
        _publish_tool(mp, "basic-tool", attestation=att_basic)
        _publish_tool(mp, "standard-tool", attestation=att_standard)
        _publish_tool(mp, "uncertified-tool")
        results = mp.search(min_certification=CertificationLevel.STANDARD)
        assert len(results) == 1
        assert results[0].tool_name == "standard-tool"

    def test_search_min_rating(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "good-tool")
        mp.add_review(listing.listing_id, reviewer_id="u1", rating=ReviewRating.FIVE)
        _publish_tool(mp, "unrated-tool")
        results = mp.search(min_rating=4.0)
        assert len(results) == 1

    def test_search_min_installs(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "popular-tool")
        for i in range(5):
            mp.install(listing.listing_id, installer_id=f"user-{i}")
        _publish_tool(mp, "unpopular-tool")
        results = mp.search(min_installs=3)
        assert len(results) == 1
        assert results[0].tool_name == "popular-tool"

    def test_search_only_published(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "draft-tool")
        mp.update_status(listing.listing_id, PublishStatus.DRAFT)
        _publish_tool(mp, "published-tool")
        results = mp.search()
        assert len(results) == 1
        assert results[0].tool_name == "published-tool"

    def test_search_with_limit(self):
        mp = _make_marketplace()
        for i in range(10):
            _publish_tool(mp, f"tool-{i}")
        results = mp.search(limit=3)
        assert len(results) == 3

    def test_search_combined_filters(self):
        mp = _make_marketplace()
        att = _make_attestation(CertificationLevel.STANDARD)
        _publish_tool(
            mp,
            "perfect-match",
            author="alice",
            categories={ToolCategory.SEARCH},
            attestation=att,
            tags={"ml"},
        )
        _publish_tool(mp, "wrong-author", author="bob", categories={ToolCategory.SEARCH})
        _publish_tool(mp, "wrong-category", author="alice", categories={ToolCategory.DATABASE})
        results = mp.search(
            author="alice",
            categories={ToolCategory.SEARCH},
            certified_only=True,
            tags={"ml"},
        )
        assert len(results) == 1
        assert results[0].tool_name == "perfect-match"


# ── Sort tests ──────────────────────────────────────────────────────


class TestToolSorting:
    """Tests for marketplace search sorting."""

    def test_sort_by_rating(self):
        mp = _make_marketplace()
        listing_a = _publish_tool(mp, "a")
        mp.add_review(listing_a.listing_id, reviewer_id="u1", rating=ReviewRating.THREE)
        listing_b = _publish_tool(mp, "b")
        mp.add_review(listing_b.listing_id, reviewer_id="u1", rating=ReviewRating.FIVE)
        results = mp.search(sort_by=SortBy.RATING)
        assert results[0].tool_name == "b"

    def test_sort_by_installs(self):
        mp = _make_marketplace()
        listing_a = _publish_tool(mp, "a")
        mp.install(listing_a.listing_id, installer_id="u1")
        listing_b = _publish_tool(mp, "b")
        for i in range(5):
            mp.install(listing_b.listing_id, installer_id=f"u{i}")
        results = mp.search(sort_by=SortBy.INSTALLS)
        assert results[0].tool_name == "b"

    def test_sort_by_newest(self):
        mp = _make_marketplace()
        _publish_tool(mp, "old-tool")
        _publish_tool(mp, "new-tool")
        results = mp.search(sort_by=SortBy.NEWEST)
        # Newest first
        assert results[0].tool_name == "new-tool"

    def test_sort_by_trust_score(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        att = _make_attestation(CertificationLevel.STRICT)
        _publish_tool(mp, "trusted-tool", attestation=att)
        _publish_tool(mp, "untrusted-tool")
        results = mp.search(sort_by=SortBy.TRUST_SCORE)
        assert results[0].tool_name == "trusted-tool"


# ── Review tests ────────────────────────────────────────────────────


class TestToolReviews:
    """Tests for the review system."""

    def test_add_review(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        review = mp.add_review(
            listing.listing_id,
            reviewer_id="user-1",
            rating=ReviewRating.FOUR,
            title="Pretty good",
            body="Works well for my use case.",
        )
        assert review is not None
        assert review.rating == ReviewRating.FOUR
        assert listing.average_rating == 4.0

    def test_add_multiple_reviews(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        mp.add_review(listing.listing_id, reviewer_id="u1", rating=ReviewRating.FIVE)
        mp.add_review(listing.listing_id, reviewer_id="u2", rating=ReviewRating.THREE)
        mp.add_review(listing.listing_id, reviewer_id="u3", rating=ReviewRating.ONE)
        assert listing.review_count == 3
        assert listing.average_rating == pytest.approx(3.0)

    def test_review_nonexistent_listing(self):
        mp = _make_marketplace()
        result = mp.add_review(
            "nonexistent", reviewer_id="u1", rating=ReviewRating.FIVE
        )
        assert result is None

    def test_get_reviews(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        mp.add_review(listing.listing_id, reviewer_id="u1", rating=ReviewRating.FIVE)
        mp.add_review(listing.listing_id, reviewer_id="u2", rating=ReviewRating.THREE)
        reviews = mp.get_reviews(listing.listing_id)
        assert len(reviews) == 2
        # Most recent first
        assert reviews[0].reviewer_id == "u2"

    def test_get_reviews_empty_listing(self):
        mp = _make_marketplace()
        assert mp.get_reviews("nonexistent") == []

    def test_verified_review(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        review = mp.add_review(
            listing.listing_id,
            reviewer_id="u1",
            rating=ReviewRating.FIVE,
            verified_user=True,
        )
        assert review is not None
        assert review.verified_user


# ── Install tracking tests ──────────────────────────────────────────


class TestInstallTracking:
    """Tests for install/uninstall tracking."""

    def test_install(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        record = mp.install(listing.listing_id, installer_id="user-1")
        assert record is not None
        assert record.active
        assert listing.install_count == 1
        assert listing.active_installs == 1

    def test_multiple_installs(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        for i in range(5):
            mp.install(listing.listing_id, installer_id=f"user-{i}")
        assert listing.install_count == 5
        assert listing.active_installs == 5

    def test_uninstall(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        mp.install(listing.listing_id, installer_id="user-1")
        result = mp.uninstall(listing.listing_id, installer_id="user-1")
        assert result
        assert listing.active_installs == 0
        assert listing.install_count == 1  # Total stays

    def test_uninstall_nonexistent(self):
        mp = _make_marketplace()
        assert not mp.uninstall("nonexistent")

    def test_install_nonexistent(self):
        mp = _make_marketplace()
        assert mp.install("nonexistent") is None

    def test_get_installs(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool")
        mp.install(listing.listing_id, installer_id="u1")
        mp.install(listing.listing_id, installer_id="u2")
        mp.uninstall(listing.listing_id, installer_id="u1")
        all_installs = mp.get_installs(listing.listing_id)
        assert len(all_installs) == 2
        active_installs = mp.get_installs(listing.listing_id, active_only=True)
        assert len(active_installs) == 1

    def test_install_with_version(self):
        mp = _make_marketplace()
        listing = _publish_tool(mp, "my-tool", version="2.0.0")
        record = mp.install(listing.listing_id, installer_id="u1", version="1.5.0")
        assert record is not None
        assert record.version == "1.5.0"


# ── Trust Registry integration tests ───────────────────────────────


class TestTrustRegistryIntegration:
    """Tests for TrustRegistry integration."""

    def test_publish_registers_in_trust_registry(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        att = _make_attestation(CertificationLevel.STANDARD)
        _publish_tool(mp, "my-tool", attestation=att, author="acme")
        record = registry.get("my-tool")
        assert record is not None
        assert record.author == "acme"

    def test_unpublish_unregisters_from_trust_registry(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        listing = _publish_tool(mp, "my-tool")
        mp.unpublish(listing.listing_id)
        assert registry.get("my-tool") is None

    def test_review_feeds_reputation(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        _publish_tool(mp, "my-tool")
        mp.add_review(
            mp.get_by_name("my-tool").listing_id,
            reviewer_id="u1",
            rating=ReviewRating.FIVE,
        )
        record = registry.get("my-tool")
        assert record is not None
        assert len(record.reputation_events) >= 1

    def test_install_feeds_reputation(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        listing = _publish_tool(mp, "my-tool")
        mp.install(listing.listing_id, installer_id="user-1")
        record = registry.get("my-tool")
        assert record is not None
        assert record.success_count >= 1

    def test_update_attestation_syncs_to_registry(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        _publish_tool(mp, "my-tool")
        att = _make_attestation(CertificationLevel.STRICT)
        listing = mp.get_by_name("my-tool")
        mp.update_attestation(listing.listing_id, att)
        record = registry.get("my-tool")
        assert record is not None
        assert record.certification_level == CertificationLevel.STRICT

    def test_sort_by_trust_score_uses_registry(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        att_strict = _make_attestation(CertificationLevel.STRICT)
        att_basic = _make_attestation(CertificationLevel.BASIC)
        _publish_tool(mp, "strict-tool", attestation=att_strict)
        _publish_tool(mp, "basic-tool", attestation=att_basic)
        results = mp.search(sort_by=SortBy.TRUST_SCORE)
        assert results[0].tool_name == "strict-tool"


# ── Event bus integration tests ─────────────────────────────────────


class TestEventBusIntegration:
    """Tests for SecurityEventBus integration."""

    def test_publish_emits_event(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=100)
        bus.subscribe(handler)
        mp = _make_marketplace(event_bus=bus)
        _publish_tool(mp, "my-tool")
        assert len(handler.events) == 1
        assert handler.events[0].layer == "tool_marketplace"
        assert "TOOL_PUBLISHED" in handler.events[0].data.get("action", "")

    def test_unpublish_emits_event(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=100)
        bus.subscribe(handler)
        mp = _make_marketplace(event_bus=bus)
        listing = _publish_tool(mp, "my-tool")
        mp.unpublish(listing.listing_id)
        assert len(handler.events) == 2  # publish + unpublish
        assert "TOOL_UNPUBLISHED" in handler.events[1].data.get("action", "")

    def test_update_emits_event(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=100)
        bus.subscribe(handler)
        mp = _make_marketplace(event_bus=bus)
        _publish_tool(mp, "my-tool", version="1.0.0")
        _publish_tool(mp, "my-tool", version="2.0.0")
        assert len(handler.events) == 2
        assert "TOOL_UPDATED" in handler.events[1].data.get("action", "")


# ── Statistics tests ────────────────────────────────────────────────


class TestStatistics:
    """Tests for marketplace statistics."""

    def test_empty_stats(self):
        mp = _make_marketplace()
        stats = mp.get_statistics()
        assert stats["total_listings"] == 0
        assert stats["published_listings"] == 0

    def test_full_stats(self):
        mp = _make_marketplace()
        att = _make_attestation(CertificationLevel.STANDARD)
        listing1 = _publish_tool(mp, "tool-a", categories={ToolCategory.SEARCH}, attestation=att)
        listing2 = _publish_tool(mp, "tool-b", categories={ToolCategory.DATABASE})
        mp.install(listing1.listing_id, installer_id="u1")
        mp.install(listing2.listing_id, installer_id="u2")
        mp.add_review(listing1.listing_id, reviewer_id="u1", rating=ReviewRating.FIVE)
        stats = mp.get_statistics()
        assert stats["total_listings"] == 2
        assert stats["published_listings"] == 2
        assert stats["certified_tools"] == 1
        assert stats["total_installs"] == 2
        assert stats["total_reviews"] == 1
        assert "search" in stats["categories"] or "data_access" in stats["categories"]


# ── Featured tools tests ────────────────────────────────────────────


class TestFeaturedTools:
    """Tests for featured/trending tools."""

    def test_get_featured_returns_certified(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        att = _make_attestation(CertificationLevel.STANDARD)
        _publish_tool(mp, "certified-tool", attestation=att)
        _publish_tool(mp, "uncertified-tool")
        featured = mp.get_featured()
        assert len(featured) == 1
        assert featured[0].tool_name == "certified-tool"

    def test_get_featured_limit(self):
        registry = TrustRegistry()
        mp = _make_marketplace(trust_registry=registry)
        att = _make_attestation(CertificationLevel.BASIC)
        for i in range(15):
            _publish_tool(mp, f"tool-{i}", attestation=att, author=f"author-{i}")
        featured = mp.get_featured(limit=5)
        assert len(featured) == 5


# ── Import tests ────────────────────────────────────────────────────


class TestImports:
    """Tests for module imports."""

    def test_gateway_imports(self):
        from fastmcp.server.security.gateway import (
            InstallRecord,
            PublishStatus,
            ReviewRating,
            SortBy,
            ToolCategory,
            ToolListing,
            ToolMarketplace,
            ToolReview,
        )

        assert ToolMarketplace is not None
        assert ToolListing is not None
        assert ToolCategory is not None
        assert PublishStatus is not None
        assert ReviewRating is not None
        assert SortBy is not None
        assert InstallRecord is not None
        assert ToolReview is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            InstallRecord,
            PublishStatus,
            ReviewRating,
            SortBy,
            ToolCategory,
            ToolListing,
            ToolMarketplace,
            ToolReview,
        )

        assert ToolMarketplace is not None
        assert ToolListing is not None
        assert ToolCategory is not None
        assert PublishStatus is not None
        assert ReviewRating is not None
        assert SortBy is not None
        assert InstallRecord is not None
        assert ToolReview is not None
