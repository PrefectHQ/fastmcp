"""Tests for Marketplace Data Bridge.

Covers the MarketplaceDataBridge class that converts ToolMarketplace
backend state into React-marketplace-compatible JSON.
"""

from __future__ import annotations

import json

import pytest

from fastmcp.server.security.gateway.marketplace_bridge import MarketplaceDataBridge
from fastmcp.server.security.gateway.tool_marketplace import (
    PublishStatus,
    ReviewRating,
    ToolCategory,
    ToolMarketplace,
)
from fastmcp.server.security.registry.registry import TrustRegistry


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture()
def empty_marketplace():
    return ToolMarketplace()


@pytest.fixture()
def registry():
    return TrustRegistry()


@pytest.fixture()
def populated_marketplace(registry):
    mp = ToolMarketplace(trust_registry=registry)

    mp.publish(
        "api-gateway",
        display_name="API Gateway",
        description="High-performance API gateway with rate limiting.",
        version="3.2.1",
        author="securecorp",
        categories={ToolCategory.NETWORK},
        tags={"gateway", "api", "proxy"},
        tool_license="MIT",
        homepage_url="https://example.com",
        source_url="https://github.com/example",
    )

    mp.publish(
        "vector-search",
        display_name="Vector Search",
        description="Semantic vector search over documents.",
        version="2.0.0",
        author="aitools",
        categories={ToolCategory.SEARCH, ToolCategory.AI_ML},
        tags={"vector", "search", "embeddings"},
        tool_license="Apache-2.0",
    )

    mp.publish(
        "pg-connector",
        display_name="PostgreSQL Connector",
        description="Direct PostgreSQL access with connection pooling.",
        version="4.1.0",
        author="dataops",
        categories={ToolCategory.DATABASE},
        tags={"postgres", "sql"},
        tool_license="MIT",
    )

    mp.publish(
        "draft-tool",
        display_name="Draft Tool",
        description="Not yet published.",
        version="0.1.0",
        author="test",
        status=PublishStatus.DRAFT,
    )

    # Add some reviews
    listing = mp.get_by_name("api-gateway")
    if listing:
        mp.add_review(
            listing.listing_id,
            reviewer_id="user-1",
            rating=ReviewRating.FIVE,
            title="Excellent tool",
            body="Works great in production.",
            verified_user=True,
        )
        mp.add_review(
            listing.listing_id,
            reviewer_id="user-2",
            rating=ReviewRating.FOUR,
            title="Good but needs docs",
            body="Solid tool, documentation could be better.",
        )

    # Record some installs
    if listing:
        mp.install(listing.listing_id, installer_id="user-1")
        mp.install(listing.listing_id, installer_id="user-2")
        mp.install(listing.listing_id, installer_id="user-3")

    vs = mp.get_by_name("vector-search")
    if vs:
        mp.install(vs.listing_id, installer_id="user-1")

    return mp


# ── Empty marketplace tests ─────────────────────────────────────


class TestEmptyMarketplace:
    def test_export_returns_dict(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        data = bridge.export()
        assert isinstance(data, dict)

    def test_export_has_all_keys(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        data = bridge.export()
        expected = {"stats", "featured", "listings", "categories", "moderation_queue", "generated_at"}
        assert expected == set(data.keys())

    def test_empty_listings(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        data = bridge.export()
        assert data["listings"] == []
        assert data["featured"] == []

    def test_empty_stats(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        stats = bridge.build_stats()
        assert stats["total_listings"] == 0
        assert stats["published"] == 0

    def test_export_json(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        j = bridge.export_json()
        parsed = json.loads(j)
        assert "stats" in parsed


# ── Populated marketplace tests ─────────────────────────────────


class TestPopulatedMarketplace:
    def test_stats(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        stats = bridge.build_stats()
        assert stats["total_listings"] == 4
        assert stats["published"] == 3  # draft-tool is not published
        assert stats["total_installs"] == 4  # 3 + 1
        assert stats["total_reviews"] == 2

    def test_listings_only_published(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listings = bridge.build_listings()
        # Default search returns only published
        assert len(listings) == 3
        names = {l["name"] for l in listings}
        assert "draft-tool" not in names

    def test_listing_card_structure(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listings = bridge.build_listings()
        card = listings[0]
        assert "id" in card
        assert "name" in card
        assert "display_name" in card
        assert "description" in card
        assert "version" in card
        assert "author" in card
        assert "categories" in card
        assert "category_labels" in card
        assert "status" in card
        assert "is_certified" in card
        assert "trust_score" in card
        assert "rating" in card
        assert "install_count" in card
        assert "tags" in card
        assert "updated_relative" in card

    def test_category_breakdown(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        cats = bridge.build_category_breakdown()
        assert len(cats) >= 1
        for cat in cats:
            assert "name" in cat
            assert "value" in cat
            assert "color" in cat
            assert "key" in cat

    def test_category_labels(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        cats = bridge.build_category_breakdown()
        cat_names = {c["name"] for c in cats}
        # At least one of Network, Search, Database should be present
        assert cat_names & {"Network", "Search", "Database", "AI / ML"}

    def test_featured(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        featured = bridge.build_featured()
        # Featured returns certified tools only, none are certified here
        # (no attestation was provided)
        assert isinstance(featured, list)

    def test_reviews(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listing = populated_marketplace.get_by_name("api-gateway")
        assert listing is not None
        reviews = bridge.build_reviews(listing.listing_id)
        assert len(reviews) == 2
        assert reviews[0]["rating"] in (4, 5)
        assert "reviewer" in reviews[0]
        assert "title" in reviews[0]
        assert "body" in reviews[0]
        assert "verified" in reviews[0]
        assert "date" in reviews[0]

    def test_reviews_empty_listing(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        reviews = bridge.build_reviews("nonexistent-id")
        assert reviews == []

    def test_listing_detail(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listing = populated_marketplace.get_by_name("api-gateway")
        assert listing is not None
        detail = bridge.build_listing_detail(listing.listing_id)
        assert detail is not None
        assert detail["name"] == "api-gateway"
        assert detail["display_name"] == "API Gateway"
        assert detail["install_count"] == 3
        assert len(detail["reviews"]) == 2
        assert "install_records" in detail

    def test_listing_detail_not_found(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        detail = bridge.build_listing_detail("nonexistent-id")
        assert detail is None


# ── Filtering tests ─────────────────────────────────────────────


class TestFiltering:
    def test_search_by_query(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        results = bridge.build_listings(query="vector")
        assert len(results) == 1
        assert results[0]["name"] == "vector-search"

    def test_search_by_category(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        results = bridge.build_listings(category=ToolCategory.DATABASE)
        assert len(results) == 1
        assert results[0]["name"] == "pg-connector"

    def test_search_no_results(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        results = bridge.build_listings(query="nonexistent-xyz")
        assert results == []


# ── Trust score enrichment tests ────────────────────────────────


class TestTrustEnrichment:
    def test_trust_score_from_registry(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listings = bridge.build_listings()
        # Tools registered via marketplace get trust scores from registry
        for listing in listings:
            assert isinstance(listing["trust_score"], float)
            assert 0 <= listing["trust_score"] <= 1.0

    def test_no_registry(self, populated_marketplace):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=None
        )
        listings = bridge.build_listings()
        for listing in listings:
            assert listing["trust_score"] == 0.0


# ── JSON export tests ───────────────────────────────────────────


class TestJsonExport:
    def test_json_roundtrip(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        j = bridge.export_json()
        parsed = json.loads(j)
        assert parsed["stats"]["published"] == 3
        assert len(parsed["listings"]) == 3

    def test_json_indent(self, empty_marketplace):
        bridge = MarketplaceDataBridge(marketplace=empty_marketplace)
        j = bridge.export_json(indent=4)
        assert "\n" in j


# ── Rating label tests ──────────────────────────────────────────


class TestRatingLabel:
    def test_rated_tool(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listings = bridge.build_listings()
        gw = next(l for l in listings if l["name"] == "api-gateway")
        assert gw["rating"] > 0
        assert gw["rating_label"] != "No ratings"

    def test_unrated_tool(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        listings = bridge.build_listings()
        pg = next(l for l in listings if l["name"] == "pg-connector")
        assert pg["rating"] == 0.0
        assert pg["rating_label"] == "No ratings"


# ── Full export tests ───────────────────────────────────────────


class TestFullExport:
    def test_export_structure(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        data = bridge.export()
        assert "stats" in data
        assert "featured" in data
        assert "listings" in data
        assert "categories" in data
        assert "generated_at" in data

    def test_export_consistency(self, populated_marketplace, registry):
        bridge = MarketplaceDataBridge(
            marketplace=populated_marketplace, trust_registry=registry
        )
        data = bridge.export()
        # Stats should match listings
        assert data["stats"]["published"] == len(data["listings"])
