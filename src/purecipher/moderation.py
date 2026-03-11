"""Moderation queue helpers for the PureCipher registry."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp.server.security.gateway.tool_marketplace import (
    ModerationAction,
    PublishStatus,
    ToolListing,
    ToolMarketplace,
)
from purecipher.models import ReviewQueueItem
from purecipher.publishers import publisher_id_from_author

QUEUE_STATUSES = (
    PublishStatus.PENDING_REVIEW,
    PublishStatus.PUBLISHED,
    PublishStatus.SUSPENDED,
)

ACTION_BY_NAME = {
    "approve": ModerationAction.APPROVE,
    "reject": ModerationAction.REJECT,
    "suspend": ModerationAction.SUSPEND,
    "unsuspend": ModerationAction.UNSUSPEND,
    "request-changes": ModerationAction.REQUEST_CHANGES,
    "request_changes": ModerationAction.REQUEST_CHANGES,
}

AVAILABLE_ACTIONS = {
    PublishStatus.PENDING_REVIEW: ("approve", "reject", "request-changes"),
    PublishStatus.PUBLISHED: ("suspend",),
    PublishStatus.SUSPENDED: ("unsuspend",),
}


def moderation_action_from_name(action_name: str) -> ModerationAction | None:
    """Translate a route-safe action name into a moderation enum."""

    return ACTION_BY_NAME.get(action_name.strip().lower())


def build_review_queue_item(
    listing: ToolListing,
    *,
    trust_lookup: Callable[[ToolListing], float | None],
) -> ReviewQueueItem:
    """Build a moderation queue projection for a listing."""

    return ReviewQueueItem(
        listing_id=listing.listing_id,
        tool_name=listing.tool_name,
        display_name=listing.display_name,
        author=listing.author,
        publisher_id=publisher_id_from_author(listing.author),
        status=listing.status.value,
        certification_level=listing.certification_level.value,
        trust_score=trust_lookup(listing),
        version=listing.version,
        updated_at=listing.updated_at.isoformat(),
        available_actions=list(AVAILABLE_ACTIONS.get(listing.status, ())),
    )


def build_review_queue(
    marketplace: ToolMarketplace,
    *,
    trust_lookup: Callable[[ToolListing], float | None],
    limit_per_status: int = 200,
) -> dict[str, list[ReviewQueueItem]]:
    """Build moderation queue sections from the marketplace."""

    sections: dict[str, list[ReviewQueueItem]] = {}
    for status in QUEUE_STATUSES:
        listings = marketplace.search(status=status, limit=limit_per_status)
        ordered = sorted(listings, key=lambda item: item.updated_at, reverse=True)
        sections[status.value] = [
            build_review_queue_item(item, trust_lookup=trust_lookup) for item in ordered
        ]
    return sections


__all__ = [
    "QUEUE_STATUSES",
    "build_review_queue",
    "build_review_queue_item",
    "moderation_action_from_name",
]
