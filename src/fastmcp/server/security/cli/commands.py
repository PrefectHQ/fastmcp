"""SecureMCP CLI command implementations.

Provides a programmatic CLI layer that can be used directly or
wired into a cyclopts/click/argparse entry point. Each command
returns a structured result dataclass for easy testing and
composability.

Example::

    cli = SecureMCPCLI(
        marketplace=marketplace,
        pipeline=pipeline,
        registry=registry,
    )

    # Certify a tool
    result = cli.certify(manifest)
    print(result.level, result.score)

    # Publish to marketplace
    pub = cli.publish(manifest, attestation=result.attestation)
    print(pub.listing_id)

    # Search marketplace
    hits = cli.search("search tools", min_certification="basic")
    for hit in hits.listings:
        print(hit.tool_name, hit.certification_level)

    # Inspect a tool
    info = cli.inspect("my-tool")
    print(info.trust_score, info.certification_level)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
    ToolAttestation,
)
from fastmcp.server.security.certification.manifest import SecurityManifest
from fastmcp.server.security.gateway.tool_marketplace import (
    PublishStatus,
    SortBy,
    ToolCategory,
    ToolListing,
    ToolMarketplace,
)
from fastmcp.server.security.registry.models import TrustScore

if TYPE_CHECKING:
    from fastmcp.server.security.certification.pipeline import CertificationPipeline
    from fastmcp.server.security.registry.registry import TrustRegistry

logger = logging.getLogger(__name__)


# ── Result dataclasses ──────────────────────────────────────────────


@dataclass
class CertifyResult:
    """Result of certifying a tool.

    Attributes:
        success: Whether certification succeeded.
        tool_name: Tool that was certified.
        level: Achieved certification level.
        score: Validation score (0.0-1.0).
        attestation: The attestation (if certified).
        findings: Validation findings (warnings, errors).
        message: Human-readable summary.
    """

    success: bool = False
    tool_name: str = ""
    level: CertificationLevel = CertificationLevel.UNCERTIFIED
    score: float = 0.0
    attestation: ToolAttestation | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "level": self.level.value,
            "score": round(self.score, 4),
            "findings": self.findings,
            "message": self.message,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class PublishResult:
    """Result of publishing a tool.

    Attributes:
        success: Whether publishing succeeded.
        listing_id: The marketplace listing ID.
        tool_name: Published tool name.
        version: Published version.
        status: Publish status.
        message: Human-readable summary.
    """

    success: bool = False
    listing_id: str = ""
    tool_name: str = ""
    version: str = ""
    status: PublishStatus = PublishStatus.DRAFT
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "listing_id": self.listing_id,
            "tool_name": self.tool_name,
            "version": self.version,
            "status": self.status.value,
            "message": self.message,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class SearchResult:
    """Result of searching the marketplace.

    Attributes:
        query: The search query used.
        total: Total matching results.
        listings: Matching tool listings.
        filters_applied: Summary of filters.
    """

    query: str = ""
    total: int = 0
    listings: list[ToolListing] = field(default_factory=list)
    filters_applied: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "query": self.query,
            "total": self.total,
            "filters": self.filters_applied,
            "listings": [listing.to_summary_dict() for listing in self.listings],
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class InspectResult:
    """Result of inspecting a tool.

    Attributes:
        found: Whether the tool was found.
        tool_name: Tool name.
        listing: Full listing data (if found in marketplace).
        trust_score: Trust score (if found in registry).
        certification_level: Current certification level.
        manifest: Security manifest (if available).
        install_count: Total installs.
        average_rating: Average review rating.
        message: Human-readable summary.
    """

    found: bool = False
    tool_name: str = ""
    listing: ToolListing | None = None
    trust_score: TrustScore | None = None
    certification_level: CertificationLevel = CertificationLevel.UNCERTIFIED
    manifest: SecurityManifest | None = None
    install_count: int = 0
    average_rating: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        result: dict[str, Any] = {
            "found": self.found,
            "tool_name": self.tool_name,
            "certification_level": self.certification_level.value,
            "install_count": self.install_count,
            "average_rating": round(self.average_rating, 2),
            "message": self.message,
        }
        if self.trust_score is not None:
            result["trust_score"] = self.trust_score.to_dict()
        if self.listing is not None:
            result["listing"] = self.listing.to_dict()
        if self.manifest is not None:
            result["manifest"] = self.manifest.to_dict()
        return result

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class UnpublishResult:
    """Result of unpublishing a tool.

    Attributes:
        success: Whether unpublishing succeeded.
        tool_name: Tool that was unpublished.
        message: Human-readable summary.
    """

    success: bool = False
    tool_name: str = ""
    message: str = ""


@dataclass
class StatusResult:
    """Result of checking marketplace/registry status.

    Attributes:
        marketplace_stats: Marketplace statistics.
        registry_stats: Trust registry statistics.
    """

    marketplace_stats: dict[str, Any] = field(default_factory=dict)
    registry_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "marketplace": self.marketplace_stats,
            "registry": self.registry_stats,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


# ── CLI implementation ──────────────────────────────────────────────


# Map string level names to CertificationLevel
_LEVEL_MAP: dict[str, CertificationLevel] = {
    "uncertified": CertificationLevel.UNCERTIFIED,
    "self_attested": CertificationLevel.SELF_ATTESTED,
    "basic": CertificationLevel.BASIC,
    "standard": CertificationLevel.STANDARD,
    "strict": CertificationLevel.STRICT,
}

# Map string sort names to SortBy
_SORT_MAP: dict[str, SortBy] = {
    "relevance": SortBy.RELEVANCE,
    "trust": SortBy.TRUST_SCORE,
    "rating": SortBy.RATING,
    "installs": SortBy.INSTALLS,
    "newest": SortBy.NEWEST,
    "updated": SortBy.RECENTLY_UPDATED,
}

# Map string category names to ToolCategory
_CATEGORY_MAP: dict[str, ToolCategory] = {cat.value: cat for cat in ToolCategory}


def _parse_level(level_str: str) -> CertificationLevel:
    """Parse a certification level string."""
    normalized = level_str.lower().strip()
    if normalized in _LEVEL_MAP:
        return _LEVEL_MAP[normalized]
    raise ValueError(
        f"Unknown certification level: {level_str!r}. "
        f"Valid levels: {', '.join(_LEVEL_MAP.keys())}"
    )


def _parse_sort(sort_str: str) -> SortBy:
    """Parse a sort-by string."""
    normalized = sort_str.lower().strip()
    if normalized in _SORT_MAP:
        return _SORT_MAP[normalized]
    raise ValueError(
        f"Unknown sort option: {sort_str!r}. "
        f"Valid options: {', '.join(_SORT_MAP.keys())}"
    )


def _parse_categories(category_strs: list[str]) -> set[ToolCategory]:
    """Parse a list of category strings."""
    result: set[ToolCategory] = set()
    for cat_str in category_strs:
        normalized = cat_str.lower().strip()
        if normalized in _CATEGORY_MAP:
            result.add(_CATEGORY_MAP[normalized])
        else:
            raise ValueError(
                f"Unknown category: {cat_str!r}. "
                f"Valid categories: {', '.join(_CATEGORY_MAP.keys())}"
            )
    return result


class SecureMCPCLI:
    """Programmatic CLI for SecureMCP operations.

    Wraps the marketplace, certification pipeline, and trust registry
    into a developer-friendly command interface. Each method returns
    a structured result dataclass.

    Args:
        marketplace: Tool marketplace instance.
        pipeline: Certification pipeline instance (optional).
        registry: Trust registry instance (optional).
    """

    def __init__(
        self,
        *,
        marketplace: ToolMarketplace | None = None,
        pipeline: CertificationPipeline | None = None,
        registry: TrustRegistry | None = None,
    ) -> None:
        self._marketplace = marketplace or ToolMarketplace()
        self._pipeline = pipeline
        self._registry = registry

    @property
    def marketplace(self) -> ToolMarketplace:
        """The underlying marketplace."""
        return self._marketplace

    @property
    def registry(self) -> TrustRegistry | None:
        """The underlying trust registry."""
        return self._registry

    def certify(
        self,
        manifest: SecurityManifest,
        *,
        requested_level: str | CertificationLevel = "standard",
    ) -> CertifyResult:
        """Certify a tool against its security manifest.

        Validates the manifest and produces an attestation if the
        tool meets the requested certification level.

        Args:
            manifest: The tool's security manifest.
            requested_level: Target certification level (string or enum).

        Returns:
            CertifyResult with certification outcome.
        """
        if self._pipeline is None:
            return CertifyResult(
                success=False,
                tool_name=manifest.tool_name,
                message="Certification pipeline not configured.",
            )

        if isinstance(requested_level, str):
            requested_level = _parse_level(requested_level)

        result = self._pipeline.certify(manifest, requested_level=requested_level)

        findings = [
            {
                "severity": f.severity.value,
                "category": f.category,
                "message": f.message,
                "field_path": f.field_path,
                "suggestion": f.suggestion,
            }
            for f in result.report.findings
        ]

        if result.is_certified:
            message = (
                f"Certified at {result.certification_level.value} "
                f"(score: {result.score:.2f})"
            )
        else:
            message = (
                f"Certification failed — score {result.score:.2f} "
                f"below threshold for {requested_level.value}"
            )

        return CertifyResult(
            success=result.is_certified,
            tool_name=manifest.tool_name,
            level=result.certification_level,
            score=result.score,
            attestation=result.attestation,
            findings=findings,
            message=message,
        )

    def publish(
        self,
        manifest: SecurityManifest,
        *,
        attestation: ToolAttestation | None = None,
        description: str = "",
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        homepage_url: str = "",
        source_url: str = "",
        tool_license: str = "",
    ) -> PublishResult:
        """Publish a tool to the marketplace.

        Args:
            manifest: Security manifest (provides name, version, author).
            attestation: Certification attestation (optional).
            description: Tool description.
            categories: Category strings.
            tags: Searchable tags.
            homepage_url: Project homepage URL.
            source_url: Source code URL.
            tool_license: SPDX license identifier.

        Returns:
            PublishResult with the listing details.
        """
        parsed_categories: set[ToolCategory] = set()
        if categories:
            parsed_categories = _parse_categories(categories)

        listing = self._marketplace.publish(
            manifest.tool_name,
            display_name=manifest.tool_name.replace("-", " ").title(),
            description=description or f"MCP tool: {manifest.tool_name}",
            version=manifest.version,
            author=manifest.author,
            categories=parsed_categories or {ToolCategory.UTILITY},
            manifest=manifest,
            attestation=attestation,
            homepage_url=homepage_url,
            source_url=source_url,
            tool_license=tool_license,
            tags=set(tags) if tags else set(),
        )

        return PublishResult(
            success=True,
            listing_id=listing.listing_id,
            tool_name=listing.tool_name,
            version=listing.version,
            status=listing.status,
            message=f"Published {listing.tool_name} v{listing.version}",
        )

    def certify_and_publish(
        self,
        manifest: SecurityManifest,
        *,
        requested_level: str | CertificationLevel = "standard",
        description: str = "",
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        homepage_url: str = "",
        source_url: str = "",
        tool_license: str = "",
    ) -> tuple[CertifyResult, PublishResult | None]:
        """Certify a tool and publish it if certification passes.

        Convenience method that chains certify → publish.

        Returns:
            Tuple of (CertifyResult, PublishResult or None if cert failed).
        """
        cert_result = self.certify(manifest, requested_level=requested_level)

        if not cert_result.success:
            return cert_result, None

        pub_result = self.publish(
            manifest,
            attestation=cert_result.attestation,
            description=description,
            categories=categories,
            tags=tags,
            homepage_url=homepage_url,
            source_url=source_url,
            tool_license=tool_license,
        )

        return cert_result, pub_result

    def search(
        self,
        query: str = "",
        *,
        categories: list[str] | None = None,
        min_certification: str | None = None,
        certified_only: bool = False,
        author: str | None = None,
        tags: list[str] | None = None,
        sort_by: str = "relevance",
        limit: int = 50,
    ) -> SearchResult:
        """Search the marketplace for tools.

        Args:
            query: Free-text search query.
            categories: Filter by categories.
            min_certification: Minimum certification level string.
            certified_only: Only return certified tools.
            author: Filter by author.
            tags: Filter by tags.
            sort_by: Sort order string.
            limit: Maximum results.

        Returns:
            SearchResult with matching listings.
        """
        parsed_categories: set[ToolCategory] | None = None
        if categories:
            parsed_categories = _parse_categories(categories)

        parsed_level: CertificationLevel | None = None
        if min_certification:
            parsed_level = _parse_level(min_certification)

        parsed_sort = _parse_sort(sort_by)

        listings = self._marketplace.search(
            query=query or None,
            categories=parsed_categories,
            min_certification=parsed_level,
            certified_only=certified_only,
            author=author,
            tags=set(tags) if tags else None,
            sort_by=parsed_sort,
            limit=limit,
        )

        filters_applied: dict[str, Any] = {}
        if query:
            filters_applied["query"] = query
        if categories:
            filters_applied["categories"] = categories
        if min_certification:
            filters_applied["min_certification"] = min_certification
        if certified_only:
            filters_applied["certified_only"] = True
        if author:
            filters_applied["author"] = author
        if tags:
            filters_applied["tags"] = tags
        filters_applied["sort_by"] = sort_by

        return SearchResult(
            query=query,
            total=len(listings),
            listings=listings,
            filters_applied=filters_applied,
        )

    def inspect(self, tool_name: str) -> InspectResult:
        """Inspect a tool's details, trust score, and certification.

        Looks up the tool in both the marketplace and the trust
        registry to provide a comprehensive view.

        Args:
            tool_name: The tool to inspect.

        Returns:
            InspectResult with detailed tool information.
        """
        listing = self._marketplace.get_by_name(tool_name)
        trust_score: TrustScore | None = None

        if self._registry is not None:
            trust_score = self._registry.get_trust_score(tool_name)

        if listing is None and trust_score is None:
            return InspectResult(
                found=False,
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found in marketplace or registry.",
            )

        cert_level = CertificationLevel.UNCERTIFIED
        manifest: SecurityManifest | None = None
        install_count = 0
        average_rating = 0.0

        if listing is not None:
            cert_level = listing.certification_level
            manifest = listing.manifest
            install_count = listing.install_count
            average_rating = listing.average_rating

        return InspectResult(
            found=True,
            tool_name=tool_name,
            listing=listing,
            trust_score=trust_score,
            certification_level=cert_level,
            manifest=manifest,
            install_count=install_count,
            average_rating=average_rating,
            message=f"Tool '{tool_name}' — {cert_level.value}",
        )

    def unpublish(self, tool_name: str) -> UnpublishResult:
        """Remove a tool from the marketplace.

        Args:
            tool_name: The tool to unpublish.

        Returns:
            UnpublishResult indicating success or failure.
        """
        listing = self._marketplace.get_by_name(tool_name)
        if listing is None:
            return UnpublishResult(
                success=False,
                tool_name=tool_name,
                message=f"Tool '{tool_name}' not found in marketplace.",
            )

        self._marketplace.unpublish(listing.listing_id)
        return UnpublishResult(
            success=True,
            tool_name=tool_name,
            message=f"Tool '{tool_name}' unpublished.",
        )

    def status(self) -> StatusResult:
        """Get marketplace and registry status.

        Returns:
            StatusResult with aggregate statistics.
        """
        marketplace_stats = self._marketplace.get_statistics()
        registry_stats: dict[str, Any] = {}

        if self._registry is not None:
            all_records = self._registry.get_all()
            total = len(all_records)
            certified = sum(1 for r in all_records if r.is_certified)
            avg_trust = 0.0
            if total > 0:
                avg_trust = sum(r.trust_score.overall for r in all_records) / total
            registry_stats = {
                "total_tools": total,
                "certified_tools": certified,
                "average_trust_score": round(avg_trust, 4),
            }

        return StatusResult(
            marketplace_stats=marketplace_stats,
            registry_stats=registry_stats,
        )

    def validate(self, manifest: SecurityManifest) -> CertifyResult:
        """Validate a manifest without creating an attestation.

        Dry-run validation — reports findings and score without
        issuing a certification. Useful for pre-flight checks.

        Args:
            manifest: The manifest to validate.

        Returns:
            CertifyResult with validation findings (no attestation).
        """
        if self._pipeline is None:
            return CertifyResult(
                success=False,
                tool_name=manifest.tool_name,
                message="Certification pipeline not configured.",
            )

        report = self._pipeline._validator.validate(manifest)

        findings = [
            {
                "severity": f.severity.value,
                "category": f.category,
                "message": f.message,
                "field_path": f.field_path,
                "suggestion": f.suggestion,
            }
            for f in report.findings
        ]

        return CertifyResult(
            success=not report.has_errors and not report.has_critical,
            tool_name=manifest.tool_name,
            level=report.max_certification_level,
            score=report.score,
            attestation=None,  # Validate-only, no attestation
            findings=findings,
            message=f"Validation {'passed' if not report.has_errors else 'failed'} — "
            f"score {report.score:.2f}, max level: {report.max_certification_level.value}",
        )

    @staticmethod
    def parse_manifest_file(manifest_json: str) -> SecurityManifest:
        """Parse a SecurityManifest from a JSON string.

        Args:
            manifest_json: JSON representation of a manifest.

        Returns:
            SecurityManifest instance.
        """
        data = json.loads(manifest_json)
        from fastmcp.server.security.certification.manifest import (
            DataClassification,
            DataFlowDeclaration,
            PermissionScope,
            ResourceAccessDeclaration,
        )

        permissions: set[PermissionScope] = set()
        for p in data.get("permissions", []):
            permissions.add(PermissionScope(p))

        data_flows: list[DataFlowDeclaration] = []
        for df in data.get("data_flows", []):
            data_flows.append(
                DataFlowDeclaration(
                    source=df["source"],
                    destination=df["destination"],
                    classification=DataClassification(
                        df.get("classification", "internal")
                    ),
                    transforms=tuple(df.get("transforms", [])),
                    retention=df.get("retention"),
                )
            )

        resource_access: list[ResourceAccessDeclaration] = []
        for ra in data.get("resource_access", []):
            resource_access.append(
                ResourceAccessDeclaration(
                    resource_pattern=ra["resource_pattern"],
                    access_type=ra.get("access_type", "read"),
                    required=ra.get("required", True),
                    classification=DataClassification(
                        ra.get("classification", "internal")
                    ),
                )
            )

        return SecurityManifest(
            tool_name=data["tool_name"],
            version=data.get("version", ""),
            author=data.get("author", ""),
            permissions=permissions,
            data_flows=data_flows,
            resource_access=resource_access,
            max_execution_time_seconds=data.get("max_execution_time_seconds"),
            idempotent=data.get("idempotent", False),
            deterministic=data.get("deterministic", False),
            requires_consent=data.get("requires_consent", False),
            dependencies=data.get("dependencies", []),
            tags=set(data.get("tags", [])),
        )
