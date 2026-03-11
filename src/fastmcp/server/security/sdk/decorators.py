"""Security decorators for tool functions.

Provides declarative security configuration that can be applied
to tool functions to enforce trust, sandbox, and provenance policies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.certification.manifest import PermissionScope


@dataclass
class SecurityDecoratorConfig:
    """Configuration for a security decorator.

    Defines what security checks and recording should be applied
    to a decorated tool function.
    """

    config_id: str = ""
    tool_name: str = ""
    required_permissions: set[PermissionScope] = field(default_factory=set)
    min_trust_score: float = 0.0
    require_certification: bool = False
    record_provenance: bool = True
    sandbox_enabled: bool = False
    max_execution_seconds: float = 30.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.config_id:
            self.config_id = f"sdcfg-{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return {
            "config_id": self.config_id,
            "tool_name": self.tool_name,
            "required_permissions": [p.value for p in self.required_permissions],
            "min_trust_score": self.min_trust_score,
            "require_certification": self.require_certification,
            "record_provenance": self.record_provenance,
            "sandbox_enabled": self.sandbox_enabled,
            "max_execution_seconds": self.max_execution_seconds,
        }


@dataclass
class SecurityDecorator:
    """Applies security policies to tool functions.

    Collects decorated functions with their security configs for
    later enforcement by the SDK client.
    """

    decorator_id: str = ""
    registered_tools: dict[str, SecurityDecoratorConfig] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.decorator_id:
            self.decorator_id = f"sd-{uuid.uuid4().hex[:8]}"

    def register(
        self,
        tool_name: str,
        config: SecurityDecoratorConfig | None = None,
        **kwargs: Any,
    ) -> SecurityDecoratorConfig:
        """Register a tool with security configuration.

        Args:
            tool_name: Name of the tool.
            config: Pre-built config, or pass kwargs to build one.

        Returns:
            The registered SecurityDecoratorConfig.
        """
        if config is None:
            config = SecurityDecoratorConfig(tool_name=tool_name, **kwargs)
        else:
            config.tool_name = tool_name
        self.registered_tools[tool_name] = config
        return config

    def unregister(self, tool_name: str) -> bool:
        """Remove a tool registration."""
        if tool_name in self.registered_tools:
            del self.registered_tools[tool_name]
            return True
        return False

    def get_config(self, tool_name: str) -> SecurityDecoratorConfig | None:
        """Get the security config for a registered tool."""
        return self.registered_tools.get(tool_name)

    @property
    def tool_count(self) -> int:
        return len(self.registered_tools)

    def get_tools_requiring_certification(self) -> list[str]:
        """Get tools that require certification."""
        return [
            name
            for name, cfg in self.registered_tools.items()
            if cfg.require_certification
        ]

    def get_tools_with_sandbox(self) -> list[str]:
        """Get tools with sandbox enabled."""
        return [
            name for name, cfg in self.registered_tools.items() if cfg.sandbox_enabled
        ]

    def to_dict(self) -> dict:
        return {
            "decorator_id": self.decorator_id,
            "tool_count": self.tool_count,
            "tools": {
                name: cfg.to_dict() for name, cfg in self.registered_tools.items()
            },
        }
