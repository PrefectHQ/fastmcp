"""Visibility filtering for FastMCP components.

This module provides the VisibilityFilter class which handles blocklist and
allowlist logic for controlling component visibility at both the provider
and server levels.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import mcp.types

if TYPE_CHECKING:
    from fastmcp.utilities.components import FastMCPComponent

_KEY_PREFIX_TO_NOTIFICATION: dict[str, type[mcp.types.ServerNotificationType]] = {
    "tool:": mcp.types.ToolListChangedNotification,
    "prompt:": mcp.types.PromptListChangedNotification,
    "resource:": mcp.types.ResourceListChangedNotification,
    "template:": mcp.types.ResourceListChangedNotification,
}


class VisibilityFilter:
    """Manages component visibility with blocklist and allowlist support.

    Both servers and providers use this class to control which components
    are visible. Visibility is hierarchical: if a component is hidden at
    any level (provider or server), it's hidden to the client.

    Filtering logic (blocklist wins over allowlist):
    1. If component key is in _disabled_keys → HIDDEN
    2. If any component tag is in _disabled_tags → HIDDEN
    3. If _default_enabled is False and component not in allowlist → HIDDEN
    4. Otherwise → VISIBLE

    The `only=True` flag on enable() switches to allowlist mode:
    - Sets _default_enabled = False
    - Clears existing allowlists
    - Adds specified keys/tags to allowlist
    """

    def __init__(self) -> None:
        self._disabled_keys: set[str] = set()
        self._disabled_tags: set[str] = set()
        self._enabled_keys: set[str] = set()  # allowlist
        self._enabled_tags: set[str] = set()  # allowlist
        self._default_enabled: bool = True

    def _notify(
        self, notifications: set[type[mcp.types.ServerNotificationType]]
    ) -> None:
        """Send notifications. No-op if called outside a request context."""
        from fastmcp.server.context import _current_context

        context = _current_context.get()
        if context is None:
            return

        for notification_cls in notifications:
            context.send_notification_sync(notification_cls())

    def _get_notifications_for_keys(
        self, keys: Sequence[str]
    ) -> set[type[mcp.types.ServerNotificationType]]:
        """Get notification classes for the given component keys."""
        notifications: set[type[mcp.types.ServerNotificationType]] = set()
        for key in keys:
            for prefix, notification_cls in _KEY_PREFIX_TO_NOTIFICATION.items():
                if key.startswith(prefix):
                    notifications.add(notification_cls)
                    break
        return notifications

    def disable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
    ) -> None:
        """Add to blocklist (hide components).

        Args:
            keys: Component keys to hide (e.g., "tool:my_tool", "resource:file://x")
            tags: Tags to hide - any component with these tags will be hidden
        """
        notifications: set[type[mcp.types.ServerNotificationType]] = set()

        if keys:
            new_keys = set(keys) - self._disabled_keys
            if new_keys:
                self._disabled_keys.update(new_keys)
                notifications.update(self._get_notifications_for_keys(list(new_keys)))

        if tags:
            new_tags = tags - self._disabled_tags
            if new_tags:
                self._disabled_tags.update(new_tags)
                notifications.update(_KEY_PREFIX_TO_NOTIFICATION.values())

        self._notify(notifications)

    def enable(
        self,
        *,
        keys: Sequence[str] | None = None,
        tags: set[str] | None = None,
        only: bool = False,
    ) -> None:
        """Remove from blocklist, or set allowlist with only=True.

        Args:
            keys: Component keys to show
            tags: Tags to show
            only: If True, switches to allowlist mode - ONLY show these keys/tags.
                This sets default visibility to False, clears existing allowlists,
                and adds the specified keys/tags to the allowlist.
        """
        notifications: set[type[mcp.types.ServerNotificationType]] = set()

        if only:
            # Allowlist mode: flip default, clear existing, add new
            was_default_enabled = self._default_enabled
            had_enabled = bool(self._enabled_keys or self._enabled_tags)

            self._default_enabled = False
            self._enabled_keys.clear()
            self._enabled_tags.clear()

            if keys:
                self._enabled_keys.update(keys)
                notifications.update(self._get_notifications_for_keys(list(keys)))
            if tags:
                self._enabled_tags.update(tags)
                notifications.update(_KEY_PREFIX_TO_NOTIFICATION.values())

            # If we changed default or had previous allowlist, notify all
            if was_default_enabled or had_enabled:
                notifications.update(_KEY_PREFIX_TO_NOTIFICATION.values())
        else:
            # Remove from blocklist
            if keys:
                removed_keys = set(keys) & self._disabled_keys
                if removed_keys:
                    self._disabled_keys -= removed_keys
                    notifications.update(
                        self._get_notifications_for_keys(list(removed_keys))
                    )
            if tags:
                removed_tags = tags & self._disabled_tags
                if removed_tags:
                    self._disabled_tags -= removed_tags
                    notifications.update(_KEY_PREFIX_TO_NOTIFICATION.values())

        self._notify(notifications)

    def reset(self) -> None:
        """Reset to default state (everything enabled, no filters)."""
        had_filters = bool(
            self._disabled_keys
            or self._disabled_tags
            or self._enabled_keys
            or self._enabled_tags
            or not self._default_enabled
        )

        self._disabled_keys.clear()
        self._disabled_tags.clear()
        self._enabled_keys.clear()
        self._enabled_tags.clear()
        self._default_enabled = True

        if had_filters:
            self._notify(set(_KEY_PREFIX_TO_NOTIFICATION.values()))

    def is_enabled(self, component: FastMCPComponent) -> bool:
        """Check if component is enabled. Blocklist wins over allowlist."""
        # Blocklist check (always disables, even if in allowlist)
        if component.key in self._disabled_keys:
            return False
        if component.tags & self._disabled_tags:
            return False

        # Allowlist check (only applies if default_enabled is False)
        if not self._default_enabled:
            if component.key in self._enabled_keys:
                return True
            return bool(component.tags & self._enabled_tags)

        return True
