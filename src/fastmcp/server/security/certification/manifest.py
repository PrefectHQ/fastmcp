"""Security Manifest for MCP tools.

A SecurityManifest is the declarative contract that tool authors write
to describe their tool's security profile: what permissions it needs,
what data it reads and writes, and what resources it accesses.

The manifest is the primary input to the certification pipeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PermissionScope(Enum):
    """Permissions a tool may request.

    Scopes define the boundaries of what a tool is allowed to do.
    A certified tool must declare all scopes it uses; any undeclared
    access at runtime triggers a policy violation.
    """

    READ_RESOURCE = "read_resource"
    WRITE_RESOURCE = "write_resource"
    CALL_TOOL = "call_tool"
    NETWORK_ACCESS = "network_access"
    FILE_SYSTEM_READ = "file_system_read"
    FILE_SYSTEM_WRITE = "file_system_write"
    ENVIRONMENT_READ = "environment_read"
    SUBPROCESS_EXEC = "subprocess_exec"
    SENSITIVE_DATA = "sensitive_data"
    CROSS_ORIGIN = "cross_origin"


class DataClassification(Enum):
    """Classification levels for data flowing through a tool.

    Tools must declare the highest classification of data they handle.
    This drives consent requirements and audit depth.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PII = "pii"
    PHI = "phi"
    FINANCIAL = "financial"


@dataclass(frozen=True)
class DataFlowDeclaration:
    """A declared data flow path through a tool.

    Every path data takes — from input parameter to output, to storage,
    to external service — must be declared. Undeclared flows are violations.

    Attributes:
        flow_id: Unique identifier for this flow.
        source: Where the data comes from (e.g., "input.query", "resource://docs").
        destination: Where it goes (e.g., "output.result", "https://api.example.com").
        classification: Highest data classification in this flow.
        description: Human-readable explanation of what this flow does.
        transforms: What transformations are applied (e.g., "hash", "encrypt", "redact").
        retention: How long data persists (e.g., "none", "session", "30d").
    """

    flow_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str = ""
    destination: str = ""
    classification: DataClassification = DataClassification.PUBLIC
    description: str = ""
    transforms: list[str] = field(default_factory=list)
    retention: str = "none"


@dataclass(frozen=True)
class ResourceAccessDeclaration:
    """A declared resource access pattern.

    Tools must declare every MCP resource URI pattern they intend
    to read from or write to.

    Attributes:
        resource_pattern: URI pattern (supports glob-style, e.g., "file://docs/*").
        access_type: "read" or "write".
        required: Whether access is essential (True) or optional (False).
        description: What the tool does with this resource.
        classification: Data classification of the resource content.
    """

    resource_pattern: str = ""
    access_type: str = "read"
    required: bool = True
    description: str = ""
    classification: DataClassification = DataClassification.INTERNAL


@dataclass
class SecurityManifest:
    """Declarative security profile for an MCP tool.

    Tool authors create a manifest describing their tool's complete
    security surface: permissions needed, data flows, resource access
    patterns, and operational constraints.

    Example::

        manifest = SecurityManifest(
            tool_name="search-documents",
            version="1.0.0",
            author="acme-corp",
            description="Full-text search across document store",
            permissions={PermissionScope.READ_RESOURCE},
            data_flows=[
                DataFlowDeclaration(
                    source="input.query",
                    destination="output.results",
                    classification=DataClassification.INTERNAL,
                    description="Search query → matching documents",
                ),
            ],
            resource_access=[
                ResourceAccessDeclaration(
                    resource_pattern="docs://*",
                    access_type="read",
                    description="Read documents for search indexing",
                ),
            ],
            max_execution_time_seconds=30,
        )

    Attributes:
        manifest_id: Unique identifier for this manifest version.
        tool_name: The MCP tool name this manifest describes.
        version: Semantic version of the tool.
        author: Identity of the tool author/publisher.
        description: What the tool does.
        permissions: Set of permission scopes the tool requires.
        data_flows: All data flow paths through the tool.
        resource_access: All resource access patterns.
        max_execution_time_seconds: Upper bound on tool execution time.
        idempotent: Whether the tool is safe to retry.
        deterministic: Whether same inputs produce same outputs.
        requires_consent: Whether user consent is needed before execution.
        dependencies: Other tools this tool may invoke.
        tags: Searchable tags for discovery.
        metadata: Additional manifest properties.
        created_at: When this manifest was created.
    """

    manifest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    version: str = "0.0.0"
    author: str = ""
    description: str = ""
    permissions: set[PermissionScope] = field(default_factory=set)
    data_flows: list[DataFlowDeclaration] = field(default_factory=list)
    resource_access: list[ResourceAccessDeclaration] = field(default_factory=list)
    max_execution_time_seconds: float = 60.0
    idempotent: bool = False
    deterministic: bool = False
    requires_consent: bool = False
    dependencies: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for signing and transport."""
        return {
            "manifest_id": self.manifest_id,
            "tool_name": self.tool_name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "permissions": sorted(p.value for p in self.permissions),
            "data_flows": [
                {
                    "flow_id": f.flow_id,
                    "source": f.source,
                    "destination": f.destination,
                    "classification": f.classification.value,
                    "description": f.description,
                    "transforms": f.transforms,
                    "retention": f.retention,
                }
                for f in self.data_flows
            ],
            "resource_access": [
                {
                    "resource_pattern": r.resource_pattern,
                    "access_type": r.access_type,
                    "required": r.required,
                    "description": r.description,
                    "classification": r.classification.value,
                }
                for r in self.resource_access
            ],
            "max_execution_time_seconds": self.max_execution_time_seconds,
            "idempotent": self.idempotent,
            "deterministic": self.deterministic,
            "requires_consent": self.requires_consent,
            "dependencies": self.dependencies,
            "tags": sorted(self.tags),
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }
