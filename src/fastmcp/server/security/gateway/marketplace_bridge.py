"""Marketplace Data Bridge — generates frontend-ready JSON from ToolMarketplace.

Converts ToolMarketplace state into the data structures consumed by the
SecureMCP Marketplace React UI. Supports full export, per-listing detail,
version history, and moderation queue.

Usage:
    bridge = MarketplaceDataBridge(marketplace=marketplace, registry=registry)
    data = bridge.export()
    json_string = bridge.export_json()
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.gateway.tool_marketplace import (
    PublishStatus,
    ToolCategory,
    ToolListing,
    ToolMarketplace,
)

# ── Colour palette for categories ───────────────────────────

_CATEGORY_COLORS: dict[str, str] = {
    "data_access": "#6366f1",
    "file_system": "#8b5cf6",
    "network": "#a78bfa",
    "code_execution": "#c4b5fd",
    "ai_ml": "#f59e0b",
    "communication": "#10b981",
    "search": "#3b82f6",
    "database": "#ec4899",
    "authentication": "#14b8a6",
    "monitoring": "#f97316",
    "utility": "#6b7280",
    "other": "#d1d5db",
}

_CATEGORY_LABELS: dict[str, str] = {
    "data_access": "Data Access",
    "file_system": "File System",
    "network": "Network",
    "code_execution": "Code Execution",
    "ai_ml": "AI / ML",
    "communication": "Communication",
    "search": "Search",
    "database": "Database",
    "authentication": "Auth",
    "monitoring": "Monitoring",
    "utility": "Utility",
    "other": "Other",
}


def _relative_time(dt: datetime) -> str:
    """Format a datetime as a relative time string."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%b %d, %Y")


def _stars_label(rating: float) -> str:
    """Return e.g. '4.5' or 'No ratings'."""
    if rating == 0:
        return "No ratings"
    return f"{rating:.1f}"


# ── Data bridge ─────────────────────────────────────────────


@dataclass
class MarketplaceDataBridge:
    """Converts ToolMarketplace state into React-UI-compatible dicts.

    The exported structure matches the React SecureMCPMarketplace component.

    Attributes:
        marketplace: The ToolMarketplace instance to read from.
        trust_registry: Optional TrustRegistry for trust score enrichment.
    """

    marketplace: ToolMarketplace
    trust_registry: Any = None  # TrustRegistry, but avoid circular import

    # ── Per-listing card data ─────────────────────────────

    def _listing_to_card(self, listing: ToolListing) -> dict[str, Any]:
        """Convert a single ToolListing to a UI card dict."""
        trust_score = 0.0
        if self.trust_registry:
            score_obj = self.trust_registry.get_trust_score(listing.tool_name)
            if score_obj is not None:
                trust_score = round(score_obj.overall, 2)

        return {
            "id": listing.listing_id,
            "name": listing.tool_name,
            "display_name": listing.display_name,
            "description": listing.description,
            "version": listing.version,
            "author": listing.author,
            "categories": [c.value for c in listing.categories],
            "category_labels": [
                _CATEGORY_LABELS.get(c.value, c.value) for c in listing.categories
            ],
            "status": listing.status.value,
            "is_certified": listing.is_certified,
            "certification_level": listing.certification_level.value,
            "trust_score": trust_score,
            "rating": round(listing.average_rating, 1),
            "rating_label": _stars_label(listing.average_rating),
            "review_count": listing.review_count,
            "install_count": listing.install_count,
            "active_installs": listing.active_installs,
            "homepage_url": listing.homepage_url,
            "source_url": listing.source_url,
            "license": listing.license,
            "tags": sorted(listing.tags),
            "created_at": listing.created_at.isoformat(),
            "updated_at": listing.updated_at.isoformat(),
            "updated_relative": _relative_time(listing.updated_at),
            "version_count": len(listing.version_history),
            "available_versions": listing.available_versions,
        }

    # ── Featured / trending ───────────────────────────────

    def build_featured(self, limit: int = 6) -> list[dict[str, Any]]:
        """Build featured tools list."""
        featured = self.marketplace.get_featured(limit=limit)
        return [self._listing_to_card(l) for l in featured]

    # ── All listings (paginated) ──────────────────────────

    def build_listings(
        self,
        *,
        status: PublishStatus | None = None,
        category: ToolCategory | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Build listing cards with optional filtering."""
        categories = {category} if category else None
        results = self.marketplace.search(
            query=query,
            categories=categories,
            status=status,
            limit=limit,
        )
        return [self._listing_to_card(l) for l in results]

    # ── Category breakdown ────────────────────────────────

    def build_category_breakdown(self) -> list[dict[str, Any]]:
        """Build category distribution for charts."""
        stats = self.marketplace.get_statistics()
        cat_counts: dict[str, int] = stats.get("categories", {})

        result = []
        for cat_val, count in sorted(
            cat_counts.items(), key=lambda x: x[1], reverse=True
        ):
            result.append(
                {
                    "name": _CATEGORY_LABELS.get(cat_val, cat_val),
                    "value": count,
                    "key": cat_val,
                    "color": _CATEGORY_COLORS.get(cat_val, "#9ca3af"),
                }
            )
        return result

    # ── Statistics banner ─────────────────────────────────

    def build_stats(self) -> dict[str, Any]:
        """Build marketplace statistics for the header."""
        stats = self.marketplace.get_statistics()
        return {
            "total_listings": stats.get("total_listings", 0),
            "published": stats.get("published_listings", 0),
            "certified": stats.get("certified_tools", 0),
            "pending_review": stats.get("pending_review", 0),
            "total_installs": stats.get("total_installs", 0),
            "total_reviews": stats.get("total_reviews", 0),
            "categories": len(stats.get("categories", {})),
        }

    # ── Reviews for a listing ─────────────────────────────

    def build_reviews(self, listing_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Build review cards for a specific listing."""
        reviews = self.marketplace.get_reviews(listing_id, limit=limit)
        return [
            {
                "id": r.review_id,
                "reviewer": r.reviewer_id,
                "rating": r.rating.value,
                "title": r.title,
                "body": r.body,
                "verified": r.verified_user,
                "date": _relative_time(r.created_at),
            }
            for r in reviews
        ]

    # ── Version history for a listing ─────────────────────

    def build_version_history(self, listing_id: str) -> list[dict[str, Any]]:
        """Build version history for a listing detail page."""
        versions = self.marketplace.get_version_history(listing_id)
        return [
            {
                "version": v.version,
                "changelog": v.changelog,
                "published_at": v.published_at.isoformat(),
                "published_relative": _relative_time(v.published_at),
                "yanked": v.yanked,
                "yank_reason": v.yank_reason,
                "manifest_digest": v.manifest_digest[:16] + "..."
                if v.manifest_digest
                else "",
            }
            for v in reversed(versions)  # newest first
        ]

    # ── Moderation queue ──────────────────────────────────

    def build_moderation_queue(self) -> list[dict[str, Any]]:
        """Build the moderation queue for moderator UI."""
        pending = self.marketplace.get_pending_review()
        return [self._listing_to_card(l) for l in pending]

    def build_moderation_log(self, listing_id: str) -> list[dict[str, Any]]:
        """Build moderation history for a listing."""
        decisions = self.marketplace.get_moderation_log(listing_id)
        return [
            {
                "decision_id": d.decision_id,
                "moderator": d.moderator_id,
                "action": d.action.value,
                "reason": d.reason,
                "date": _relative_time(d.created_at),
                "created_at": d.created_at.isoformat(),
            }
            for d in reversed(decisions)  # newest first
        ]

    # ── Listing detail page ───────────────────────────────

    def build_listing_detail(self, listing_id: str) -> dict[str, Any] | None:
        """Build full detail view for a single listing."""
        listing = self.marketplace.get(listing_id)
        if listing is None:
            return None

        card = self._listing_to_card(listing)
        card["reviews"] = self.build_reviews(listing_id)
        card["version_history"] = self.build_version_history(listing_id)
        card["moderation_log"] = self.build_moderation_log(listing_id)
        card["installs"] = self.marketplace.get_installs(listing_id)
        card["install_records"] = len(card["installs"])
        del card["installs"]  # Don't expose raw install records to frontend
        return card

    # ── Full export ───────────────────────────────────────

    def export(self) -> dict[str, Any]:
        """Export all marketplace data as a single dict.

        Returns::

            {
                "stats": {...},
                "featured": [...],
                "listings": [...],
                "categories": [...],
                "moderation_queue": [...],
                "generated_at": "...",
            }
        """
        return {
            "stats": self.build_stats(),
            "featured": self.build_featured(),
            "listings": self.build_listings(),
            "categories": self.build_category_breakdown(),
            "moderation_queue": self.build_moderation_queue(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def export_json(self, indent: int = 2) -> str:
        """Export as a JSON string."""
        return json.dumps(self.export(), indent=indent, default=str)
