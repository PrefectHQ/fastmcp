"""
Security capability definitions for FastMCP tools.

This module provides a centralized location for declaring tool
capabilities and determining their associated security risk levels.

The goal is to make potentially sensitive tool actions visible to:

- MCP server developers
- MCP clients
- Future permission and confirmation systems

Examples
--------
A tool that can execute shell commands:

    @mcp.tool(
        capabilities=[ToolCapability.SHELL_EXECUTE]
    )
    def run_command(command: str) -> str:
        ...

A tool that can delete files:

    @mcp.tool(
        capabilities=[
            ToolCapability.FILESYSTEM_READ,
            ToolCapability.FILESYSTEM_DELETE,
        ]
    )
    def delete_directory(path: str) -> None:
        ...

The effective risk level of a tool is calculated from the highest-risk
capability assigned to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias


class ToolCapability(str, Enum):
    """
    Security-sensitive capabilities that a tool may possess.

    Capabilities describe WHAT a tool is allowed to do rather than
    HOW it performs the action.

    These values may later be surfaced through MCP metadata,
    permission systems, approval workflows, or security dashboards.
    """

    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    FILESYSTEM_DELETE = "filesystem:delete"

    ENVIRONMENT_READ = "environment:read"

    NETWORK_READ = "network:read"
    NETWORK_WRITE = "network:write"

    DATABASE_READ = "database:read"
    DATABASE_WRITE = "database:write"

    SHELL_EXECUTE = "shell:execute"


class ToolRiskLevel(str, Enum):
    """
    Security risk classification for a tool.

    Risk levels are ordered from least dangerous to most dangerous.

    The highest-risk capability assigned to a tool determines the
    overall tool risk classification.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


ToolCapabilityInput: TypeAlias = ToolCapability | str


#
# Numeric severity ranking used for comparisons.
#
# Example:
#     HIGH > MEDIUM
#     CRITICAL > HIGH
#
_RISK_PRIORITY = {
    ToolRiskLevel.LOW: 1,
    ToolRiskLevel.MEDIUM: 2,
    ToolRiskLevel.HIGH: 3,
    ToolRiskLevel.CRITICAL: 4,
}


#
# Maps a capability to its default security risk.
#
# These values can be refined later as the capability model evolves.
#
CAPABILITY_RISK_MAPPING = {
    ToolCapability.FILESYSTEM_READ: ToolRiskLevel.LOW,
    ToolCapability.NETWORK_READ: ToolRiskLevel.LOW,
    ToolCapability.DATABASE_READ: ToolRiskLevel.LOW,

    ToolCapability.FILESYSTEM_WRITE: ToolRiskLevel.MEDIUM,
    ToolCapability.NETWORK_WRITE: ToolRiskLevel.MEDIUM,

    ToolCapability.ENVIRONMENT_READ: ToolRiskLevel.HIGH,
    ToolCapability.DATABASE_WRITE: ToolRiskLevel.HIGH,

    ToolCapability.FILESYSTEM_DELETE: ToolRiskLevel.CRITICAL,
    ToolCapability.SHELL_EXECUTE: ToolRiskLevel.CRITICAL,
}


@dataclass(frozen=True)
class ToolSecurityMetadata:
    """
    Security metadata associated with a registered tool.

    Attributes
    ----------
    capabilities:
        Declared capabilities exposed by the tool.

    risk_level:
        Computed overall risk classification.
    """

    capabilities: tuple[ToolCapability, ...]
    risk_level: ToolRiskLevel


def normalize_tool_capabilities(
    capabilities: Sequence[ToolCapabilityInput] | None,
) -> list[ToolCapability] | None:
    """
    Normalize user-provided capability values.

    The public decorator accepts either ``ToolCapability`` members or their
    string values. Normalizing early gives users a clear error for invalid
    capability strings and keeps the stored tool model consistent.
    """

    if capabilities is None:
        return None

    normalized: list[ToolCapability] = []
    seen: set[ToolCapability] = set()

    for capability in capabilities:
        parsed = (
            capability
            if isinstance(capability, ToolCapability)
            else ToolCapability(capability)
        )
        if parsed not in seen:
            normalized.append(parsed)
            seen.add(parsed)

    return normalized


def get_capability_risk(
    capability: ToolCapability,
) -> ToolRiskLevel:
    """
    Return the risk level associated with a capability.

    Parameters
    ----------
    capability:
        Capability whose risk should be evaluated.

    Returns
    -------
    ToolRiskLevel
        Risk classification for the capability.
    """

    return CAPABILITY_RISK_MAPPING.get(
        capability,
        ToolRiskLevel.LOW,
    )


def calculate_tool_risk(
    capabilities: Sequence[ToolCapabilityInput] | None,
) -> ToolRiskLevel:
    """
    Calculate the effective risk level for a tool.

    The overall tool risk is determined by the highest-risk
    capability declared by the tool.

    Examples
    --------
    [FILESYSTEM_READ]
        -> LOW

    [FILESYSTEM_READ, FILESYSTEM_WRITE]
        -> MEDIUM

    [FILESYSTEM_DELETE]
        -> CRITICAL
    """

    normalized = normalize_tool_capabilities(capabilities)

    if not normalized:
        return ToolRiskLevel.LOW

    highest_risk = ToolRiskLevel.LOW

    for capability in normalized:
        capability_risk = get_capability_risk(capability)

        if (
            _RISK_PRIORITY[capability_risk]
            > _RISK_PRIORITY[highest_risk]
        ):
            highest_risk = capability_risk

    return highest_risk


def build_security_metadata(
    capabilities: Sequence[ToolCapabilityInput],
) -> ToolSecurityMetadata:
    """
    Construct tool security metadata.

    Parameters
    ----------
    capabilities:
        Declared capabilities for a tool.

    Returns
    -------
    ToolSecurityMetadata
        Immutable metadata object containing capabilities
        and the computed risk level.
    """

    normalized = normalize_tool_capabilities(capabilities) or []

    return ToolSecurityMetadata(
        capabilities=tuple(normalized),
        risk_level=calculate_tool_risk(normalized),
    )


def security_metadata_to_meta_dict(
    security_metadata: ToolSecurityMetadata,
) -> dict[str, Any]:
    """Convert security metadata to the public FastMCP ``_meta`` shape."""

    return {
        "capabilities": [
            capability.value for capability in security_metadata.capabilities
        ],
        "riskLevel": security_metadata.risk_level.value,
    }
