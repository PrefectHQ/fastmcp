"""PureCipher Verified Registry MVP built on SecureMCP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic
from urllib.parse import urlencode

from mcp.server.lowlevel.server import LifespanResultT
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from fastmcp.server.security.certification.attestation import CertificationLevel
from fastmcp.server.security.certification.manifest import (
    DataClassification,
    DataFlowDeclaration,
    PermissionScope,
    ResourceAccessDeclaration,
    SecurityManifest,
)
from fastmcp.server.security.certification.pipeline import CertificationPipeline
from fastmcp.server.security.contracts.crypto import (
    ContractCryptoHandler,
    SigningAlgorithm,
)
from fastmcp.server.security.gateway.tool_marketplace import (
    PublishStatus,
    ToolCategory,
    ToolListing,
    ToolMarketplace,
)
from fastmcp.server.security.orchestrator import SecurityContext
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.storage.sqlite import SQLiteBackend
from fastmcp.utilities.ui import create_secure_html_response
from purecipher.auth import RegistryAuthSettings, RegistryRole, RegistrySession
from purecipher.install import build_install_recipes
from purecipher.moderation import build_review_queue, moderation_action_from_name
from purecipher.publishers import (
    get_public_publisher_profile,
    list_public_publishers,
    publisher_id_from_author,
)
from purecipher.ui import (
    SAMPLE_MANIFEST_JSON,
    create_listing_detail_html,
    create_login_html,
    create_publisher_index_html,
    create_publisher_profile_html,
    create_registry_ui_html,
    create_review_queue_html,
)
from securemcp import SecureMCP
from securemcp.config import (
    AlertConfig,
    CertificationConfig,
    RegistryConfig,
    SecurityConfig,
    ToolMarketplaceConfig,
)


@dataclass
class RegistrySubmissionResult:
    """Outcome of a registry submission attempt."""

    accepted: bool
    reason: str
    report: Any
    attestation: Any
    manifest_digest: str
    listing: ToolListing | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the submission result for HTTP responses."""

        payload = {
            "accepted": self.accepted,
            "reason": self.reason,
            "manifest_digest": self.manifest_digest,
            "report": self.report.to_dict(),
            "attestation": self.attestation.to_dict(),
        }
        if self.listing is not None:
            payload["listing"] = self.listing.to_dict()
        return payload


def _parse_manifest(data: dict[str, Any]) -> SecurityManifest:
    """Build a SecurityManifest from plain JSON-compatible input."""

    permissions = {PermissionScope(value) for value in data.get("permissions", [])}
    data_flows = [
        DataFlowDeclaration(
            source=flow["source"],
            destination=flow["destination"],
            classification=DataClassification(
                flow.get("classification", DataClassification.INTERNAL.value)
            ),
            description=flow.get("description", ""),
            transforms=list(flow.get("transforms", [])),
            retention=flow.get("retention", "none"),
        )
        for flow in data.get("data_flows", [])
    ]
    resource_access = [
        ResourceAccessDeclaration(
            resource_pattern=access["resource_pattern"],
            access_type=access.get("access_type", "read"),
            required=access.get("required", True),
            description=access.get("description", ""),
            classification=DataClassification(
                access.get("classification", DataClassification.INTERNAL.value)
            ),
        )
        for access in data.get("resource_access", [])
    ]
    return SecurityManifest(
        tool_name=data.get("tool_name", ""),
        version=data.get("version", "0.0.0"),
        author=data.get("author", ""),
        description=data.get("description", ""),
        permissions=permissions,
        data_flows=data_flows,
        resource_access=resource_access,
        max_execution_time_seconds=data.get("max_execution_time_seconds", 60.0),
        idempotent=data.get("idempotent", False),
        deterministic=data.get("deterministic", False),
        requires_consent=data.get("requires_consent", False),
        dependencies=list(data.get("dependencies", [])),
        tags=set(data.get("tags", [])),
        metadata=dict(data.get("metadata", {})),
    )


def _coerce_level(
    value: CertificationLevel | str | None,
    *,
    default: CertificationLevel | None = None,
) -> CertificationLevel | None:
    if value is None:
        return default
    if isinstance(value, CertificationLevel):
        return value
    return CertificationLevel(value)


def _coerce_categories(values: list[str] | set[str] | None) -> set[ToolCategory]:
    if not values:
        return set()
    return {ToolCategory(value) for value in values}


def _split_multi_value(request: Request, key: str) -> list[str]:
    values = list(request.query_params.getlist(key))
    if not values:
        raw = request.query_params.get(key)
        if raw:
            values = raw.split(",")
    return [value.strip() for value in values if value.strip()]


def _status_code_from_payload(payload: dict[str, Any]) -> int:
    status = payload.get("status")
    return status if isinstance(status, int) else 200


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    return "application/json" in accept or "application/json" in content_type


def _safe_next_path(raw_value: str | None, *, default: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return default
    if not value.startswith("/") or value.startswith("//"):
        return default
    return value


def _limit_list_payload(
    payload: dict[str, Any], *, key: str, limit: int
) -> dict[str, Any]:
    items = list(payload.get(key, []))
    trimmed = dict(payload)
    trimmed[key] = items[:limit]
    trimmed["count"] = min(len(items), limit)
    return trimmed


class PureCipherRegistry(SecureMCP[LifespanResultT], Generic[LifespanResultT]):
    """PureCipher's verified SecureMCP registry MVP.

    This wraps SecureMCP into an opinionated registry service: tools submit
    manifests, PureCipher certifies them, stores trust metadata, and exposes
    a small HTTP surface for registry queries.
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        signing_secret: bytes | str | None = None,
        issuer_id: str = "purecipher-registry",
        minimum_certification: CertificationLevel = CertificationLevel.BASIC,
        registry_prefix: str = "/registry",
        mount_registry_api: bool = True,
        mount_security_api: bool = True,
        require_moderation: bool = False,
        persistence_path: str | None = None,
        security: SecurityConfig | None = None,
        auth_settings: RegistryAuthSettings | None = None,
        **kwargs: Any,
    ) -> None:
        if security is None and signing_secret is None:
            raise ValueError(
                "`signing_secret` is required when `security` is not provided."
            )

        self._issuer_id = issuer_id
        self._minimum_certification = minimum_certification
        self._require_moderation = require_moderation
        self._registry_prefix = registry_prefix
        self._registry_api_mounted = False
        self._auth_settings = auth_settings or RegistryAuthSettings.from_env(
            issuer=issuer_id,
            signing_secret=signing_secret,
        )
        self._auth_settings.validate()

        resolved_security = security or self._build_default_security(
            signing_secret=signing_secret,
            issuer_id=issuer_id,
            minimum_certification=minimum_certification,
            require_moderation=require_moderation,
            persistence_path=persistence_path,
        )

        super().__init__(
            name=name or "purecipher-registry",
            security=resolved_security,
            mount_security_api=mount_security_api,
            **kwargs,
        )

        self._validate_registry_context(self._required_context())

        if mount_registry_api:
            self.mount_registry_api(prefix=registry_prefix)

    @property
    def minimum_certification(self) -> CertificationLevel:
        """Minimum certification level accepted by this registry."""

        return self._minimum_certification

    @property
    def auth_enabled(self) -> bool:
        """Return True when registry auth is enabled."""

        return self._auth_settings.enabled

    @staticmethod
    def _build_default_security(
        *,
        signing_secret: bytes | str | None,
        issuer_id: str,
        minimum_certification: CertificationLevel,
        require_moderation: bool,
        persistence_path: str | None,
    ) -> SecurityConfig:
        secret_bytes = (
            signing_secret.encode()
            if isinstance(signing_secret, str)
            else signing_secret
        )
        if secret_bytes is None:
            raise ValueError("`signing_secret` must resolve to bytes.")

        backend = SQLiteBackend(persistence_path) if persistence_path else None
        registry = TrustRegistry()
        marketplace = ToolMarketplace(
            trust_registry=registry,
            backend=backend,
            require_moderation=require_moderation,
        )
        crypto = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=secret_bytes,
        )

        return SecurityConfig(
            alerts=AlertConfig(),
            registry=RegistryConfig(registry=registry),
            tool_marketplace=ToolMarketplaceConfig(marketplace=marketplace),
            certification=CertificationConfig(
                pipeline=CertificationPipeline(
                    issuer_id=issuer_id,
                    crypto_handler=crypto,
                    min_level_for_signing=minimum_certification,
                )
            ),
        )

    def _required_context(self) -> SecurityContext:
        ctx = self.security_context
        if ctx is None:
            raise RuntimeError("SecureMCP is not attached to this PureCipher registry.")
        return ctx

    @staticmethod
    def _validate_registry_context(ctx: SecurityContext) -> None:
        missing: list[str] = []
        if ctx.registry is None:
            missing.append("registry")
        if ctx.tool_marketplace is None:
            missing.append("tool_marketplace")
        if ctx.certification_pipeline is None:
            missing.append("certification_pipeline")
        if missing:
            raise ValueError(
                "PureCipherRegistry requires SecureMCP components: "
                + ", ".join(missing)
            )

    def _serialize_verification(self, verification: Any) -> dict[str, Any]:
        return {
            "valid": verification.valid,
            "signature_valid": verification.signature_valid,
            "manifest_match": verification.manifest_match,
            "issues": list(verification.issues),
        }

    def _marketplace(self) -> ToolMarketplace:
        ctx = self._required_context()
        if ctx.tool_marketplace is None:
            raise RuntimeError("Tool marketplace is not attached to this registry.")
        return ctx.tool_marketplace

    def _trust_overall(self, listing: ToolListing) -> float | None:
        ctx = self._required_context()
        score = (
            ctx.registry.get_trust_score(listing.tool_name) if ctx.registry else None
        )
        return score.overall if score is not None else None

    def _serialize_listing_detail(self, listing: ToolListing) -> dict[str, Any]:
        ctx = self._required_context()
        score = (
            ctx.registry.get_trust_score(listing.tool_name) if ctx.registry else None
        )
        verification = None
        if listing.attestation is not None and ctx.certification_pipeline is not None:
            verification = self._serialize_verification(
                ctx.certification_pipeline.verify_attestation(
                    listing.attestation,
                    listing.manifest,
                )
            )

        return {
            **listing.to_dict(),
            "manifest": listing.manifest.to_dict()
            if listing.manifest is not None
            else None,
            "attestation": (
                listing.attestation.to_dict()
                if listing.attestation is not None
                else None
            ),
            "trust_score": score.to_dict() if score is not None else None,
            "verification": verification,
            "publisher_id": publisher_id_from_author(listing.author),
            "status": listing.status.value,
            "moderation_log": [
                decision.to_dict() for decision in listing.moderation_log
            ],
        }

    def _session_from_request(self, request: Request) -> RegistrySession | None:
        if not self.auth_enabled:
            return None

        token = request.cookies.get(self._auth_settings.cookie_name, "")
        if not token:
            authorization = request.headers.get("authorization", "")
            scheme, _, candidate = authorization.partition(" ")
            if scheme.lower() == "bearer":
                token = candidate.strip()
        if not token:
            return None
        return self._auth_settings.decode_token(token)

    @staticmethod
    def _session_payload(session: RegistrySession | None) -> dict[str, Any] | None:
        return session.to_dict() if session is not None else None

    @staticmethod
    def _has_roles(
        session: RegistrySession | None,
        allowed_roles: set[RegistryRole],
    ) -> bool:
        return session is not None and session.role in allowed_roles

    def _moderation_roles_for_action(
        self,
        action_name: str,
    ) -> set[RegistryRole]:
        normalized = action_name.strip().lower().replace("_", "-")
        if normalized in {"approve", "reject", "request-changes"}:
            return {RegistryRole.REVIEWER, RegistryRole.ADMIN}
        if normalized in {"suspend", "unsuspend"}:
            return {RegistryRole.ADMIN}
        return set()

    def _filter_queue_for_session(
        self,
        queue: dict[str, Any],
        session: RegistrySession | None,
    ) -> dict[str, Any]:
        filtered_sections: dict[str, list[dict[str, Any]]] = {}
        for section_name, items in (queue.get("sections") or {}).items():
            section_items: list[dict[str, Any]] = []
            for item in items:
                filtered_item = dict(item)
                filtered_item["available_actions"] = [
                    action
                    for action in list(item.get("available_actions") or [])
                    if self._has_roles(
                        session,
                        self._moderation_roles_for_action(str(action)),
                    )
                ]
                section_items.append(filtered_item)
            filtered_sections[section_name] = section_items
        return {
            **queue,
            "sections": filtered_sections,
        }

    def _set_auth_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            self._auth_settings.cookie_name,
            token,
            max_age=self._auth_settings.token_ttl_seconds,
            httponly=True,
            samesite="lax",
        )

    def _clear_auth_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self._auth_settings.cookie_name,
            httponly=True,
            samesite="lax",
        )

    def _login_redirect(self, request: Request, *, prefix: str) -> RedirectResponse:
        next_path = _safe_next_path(
            str(request.url.path)
            + (f"?{request.url.query}" if request.url.query else ""),
            default=prefix,
        )
        return RedirectResponse(
            url=f"{prefix}/login?{urlencode({'next': next_path})}",
            status_code=303,
        )

    def _render_login_ui(
        self,
        *,
        prefix: str,
        next_path: str,
        session: RegistrySession | None = None,
        notice_title: str | None = None,
        notice_body: str | None = None,
        notice_is_error: bool = False,
    ) -> str:
        return create_login_html(
            server_name=self.name,
            registry_prefix=prefix,
            auth_enabled=self.auth_enabled,
            session=self._session_payload(session),
            next_path=next_path,
            notice_title=notice_title,
            notice_body=notice_body,
            notice_is_error=notice_is_error,
        )

    def get_registry_health(self) -> dict[str, Any]:
        """Return PureCipher registry health and counts."""

        ctx = self._required_context()
        published = ctx.tool_marketplace.search(
            certified_only=True,
            min_certification=self.minimum_certification,
            limit=10_000,
        )
        pending = ctx.tool_marketplace.search(
            status=PublishStatus.PENDING_REVIEW,
            limit=10_000,
        )
        return {
            "service": "purecipher-verified-registry",
            "server": self.name,
            "status": "ok",
            "issuer_id": self._issuer_id,
            "minimum_certification": self.minimum_certification.value,
            "require_moderation": self._require_moderation,
            "auth_enabled": self.auth_enabled,
            "registered_tools": ctx.registry.record_count,
            "verified_tools": len(published),
            "pending_review": len(pending),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def list_verified_tools(
        self,
        *,
        query: str | None = None,
        author: str | None = None,
        tags: set[str] | None = None,
        categories: set[ToolCategory] | None = None,
        min_certification: CertificationLevel | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search the published verified catalog."""

        ctx = self._required_context()
        level = min_certification or self.minimum_certification
        listings = ctx.tool_marketplace.search(
            query=query,
            author=author,
            tags=tags,
            categories=categories,
            certified_only=True,
            min_certification=level,
            limit=limit,
        )
        return {
            "count": len(listings),
            "tools": [self._serialize_listing_detail(listing) for listing in listings],
            "minimum_certification": level.value,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_verified_tool(self, tool_name: str) -> dict[str, Any]:
        """Return detail for a single verified tool."""

        listing = self._marketplace().get_by_name(tool_name)
        if listing is None:
            return {"error": f"Tool '{tool_name}' not found", "status": 404}

        return self._serialize_listing_detail(listing)

    def list_publishers(self, *, limit: int = 200) -> dict[str, Any]:
        """Return public publisher summaries for published listings."""

        profiles = list_public_publishers(
            self._marketplace(),
            trust_lookup=self._trust_overall,
            listing_serializer=self._serialize_listing_detail,
            limit=limit,
        )
        return {
            "count": len(profiles),
            "publishers": [profile.summary.to_dict() for profile in profiles],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_publisher_profile(self, publisher_id: str) -> dict[str, Any]:
        """Return a public publisher profile."""

        profile = get_public_publisher_profile(
            self._marketplace(),
            publisher_id=publisher_id,
            trust_lookup=self._trust_overall,
            listing_serializer=self._serialize_listing_detail,
        )
        if profile is None:
            return {
                "error": f"Publisher '{publisher_id}' not found",
                "status": 404,
            }
        return profile.to_dict()

    def get_install_recipes(
        self,
        tool_name: str,
        *,
        registry_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Return generated install recipes for a verified tool listing."""

        listing = self._marketplace().get_by_name(tool_name)
        if listing is None:
            return {"error": f"Tool '{tool_name}' not found", "status": 404}

        recipes = build_install_recipes(
            listing,
            registry_prefix=self._registry_prefix,
            registry_base_url=registry_base_url,
        )
        return {
            "tool_name": listing.tool_name,
            "display_name": listing.display_name,
            "version": listing.version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recipes": [recipe.to_dict() for recipe in recipes],
        }

    def get_moderation_queue(self) -> dict[str, Any]:
        """Return moderation queue sections for pending and managed listings."""

        sections = build_review_queue(
            self._marketplace(),
            trust_lookup=self._trust_overall,
        )
        counts = {section: len(items) for section, items in sections.items()}
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "require_moderation": self._marketplace().require_moderation,
            "counts": counts,
            "sections": {
                section: [item.to_dict() for item in items]
                for section, items in sections.items()
            },
        }

    def moderate_listing(
        self,
        listing_id: str,
        *,
        action_name: str,
        moderator_id: str = "purecipher-admin",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a moderation action to a listing."""

        action = moderation_action_from_name(action_name)
        if action is None:
            return {
                "error": f"Unknown moderation action '{action_name}'",
                "status": 400,
            }

        marketplace = self._marketplace()
        decision = marketplace.moderate(
            listing_id,
            moderator_id=moderator_id or "purecipher-admin",
            action=action,
            reason=reason,
            metadata=metadata,
        )
        if decision is None:
            return {"error": f"Listing '{listing_id}' not found", "status": 404}

        listing = marketplace.get(listing_id)
        payload = {
            "decision": decision.to_dict(),
            "action": action.value,
            "listing": (
                self._serialize_listing_detail(listing) if listing is not None else None
            ),
        }
        return payload

    def submit_tool(
        self,
        manifest: SecurityManifest,
        *,
        display_name: str = "",
        description: str = "",
        categories: set[ToolCategory] | None = None,
        homepage_url: str = "",
        source_url: str = "",
        tool_license: str = "",
        tags: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
        changelog: str = "",
        requested_level: CertificationLevel | None = None,
    ) -> RegistrySubmissionResult:
        """Certify and publish a tool into the PureCipher registry."""

        ctx = self._required_context()
        result = ctx.certification_pipeline.certify(
            manifest,
            requested_level=requested_level,
        )
        level_order = list(CertificationLevel)
        meets_minimum = level_order.index(
            result.certification_level
        ) >= level_order.index(self.minimum_certification)

        if not meets_minimum:
            return RegistrySubmissionResult(
                accepted=False,
                reason=(
                    "Tool does not meet the minimum certification level required "
                    f"by the registry ({self.minimum_certification.value})."
                ),
                report=result.report,
                attestation=result.attestation,
                manifest_digest=result.manifest_digest,
            )

        if not result.is_certified:
            return RegistrySubmissionResult(
                accepted=False,
                reason="Tool certification did not produce a valid attestation.",
                report=result.report,
                attestation=result.attestation,
                manifest_digest=result.manifest_digest,
            )

        listing = ctx.tool_marketplace.publish(
            manifest.tool_name,
            display_name=display_name or manifest.tool_name,
            description=description or manifest.description,
            version=manifest.version,
            author=manifest.author,
            categories=categories,
            manifest=manifest,
            attestation=result.attestation,
            homepage_url=homepage_url,
            source_url=source_url,
            tool_license=tool_license,
            tags=tags or manifest.tags,
            metadata={
                "issuer_id": self._issuer_id,
                "verified_registry": "purecipher",
                **(metadata or {}),
            },
            changelog=changelog,
        )

        return RegistrySubmissionResult(
            accepted=True,
            reason="Accepted into the PureCipher verified registry.",
            report=result.report,
            attestation=result.attestation,
            manifest_digest=result.manifest_digest,
            listing=listing,
        )

    def verify_tool(
        self,
        tool_name: str,
        *,
        manifest: SecurityManifest | None = None,
    ) -> dict[str, Any]:
        """Verify a registered tool's attestation."""

        ctx = self._required_context()
        listing = ctx.tool_marketplace.get_by_name(tool_name)
        if listing is None:
            return {"error": f"Tool '{tool_name}' not found", "status": 404}
        if listing.attestation is None:
            return {"error": f"Tool '{tool_name}' has no attestation", "status": 400}

        verification = ctx.certification_pipeline.verify_attestation(
            listing.attestation,
            manifest or listing.manifest,
        )
        return {
            "tool_name": tool_name,
            "verification": self._serialize_verification(verification),
            "attestation": listing.attestation.to_dict(),
        }

    def _render_registry_ui(
        self,
        *,
        prefix: str,
        query: str = "",
        min_certification: str = "",
        selected_tool: str = "",
        manifest_text: str = SAMPLE_MANIFEST_JSON,
        display_name: str = "Weather Lookup",
        categories: str = "network,utility",
        requested_level: str = CertificationLevel.BASIC.value,
        session: RegistrySession | None = None,
        submission_title: str | None = None,
        submission_body: str | None = None,
        submission_is_error: bool = False,
        page_notice_title: str | None = None,
        page_notice_body: str | None = None,
        page_notice_is_error: bool = False,
    ) -> str:
        level = _coerce_level(
            min_certification or None,
            default=self.minimum_certification,
        )
        catalog = self.list_verified_tools(
            query=query or None,
            min_certification=level,
        )
        detail = None
        if selected_tool:
            maybe_detail = self.get_verified_tool(selected_tool)
            if "error" not in maybe_detail:
                detail = maybe_detail

        return create_registry_ui_html(
            server_name=self.name,
            registry_prefix=prefix,
            health=self.get_registry_health(),
            catalog=catalog,
            publishers=_limit_list_payload(
                self.list_publishers(limit=6),
                key="publishers",
                limit=6,
            ),
            queue=self.get_moderation_queue(),
            detail=detail,
            query=query,
            min_certification=min_certification,
            manifest_text=manifest_text,
            display_name=display_name,
            categories=categories,
            requested_level=requested_level,
            auth_enabled=self.auth_enabled,
            session=self._session_payload(session),
            submission_title=submission_title,
            submission_body=submission_body,
            submission_is_error=submission_is_error,
            page_notice_title=page_notice_title,
            page_notice_body=page_notice_body,
            page_notice_is_error=page_notice_is_error,
        )

    def _render_review_queue_ui(
        self,
        *,
        prefix: str,
        session: RegistrySession | None = None,
        notice_title: str | None = None,
        notice_body: str | None = None,
        notice_is_error: bool = False,
    ) -> str:
        return create_review_queue_html(
            server_name=self.name,
            registry_prefix=prefix,
            queue=self._filter_queue_for_session(
                self.get_moderation_queue(),
                session,
            ),
            auth_enabled=self.auth_enabled,
            session=self._session_payload(session),
            notice_title=notice_title,
            notice_body=notice_body,
            notice_is_error=notice_is_error,
        )

    def mount_registry_api(self, *, prefix: str = "/registry") -> PureCipherRegistry:
        """Mount the PureCipher registry HTTP routes."""

        if self._registry_api_mounted:
            return self

        @self.custom_route(f"{prefix}/health", methods=["GET"])
        async def registry_health(request: Request) -> JSONResponse:
            return JSONResponse(self.get_registry_health())

        @self.custom_route(f"{prefix}/session", methods=["GET"])
        async def registry_session(request: Request) -> JSONResponse:
            session = self._session_from_request(request)
            return JSONResponse(
                {
                    "auth_enabled": self.auth_enabled,
                    "session": self._session_payload(session),
                }
            )

        @self.custom_route(f"{prefix}/login", methods=["GET"])
        async def registry_login_page(request: Request):
            next_path = _safe_next_path(
                request.query_params.get("next"),
                default=prefix,
            )
            session = self._session_from_request(request)
            if not self.auth_enabled:
                return RedirectResponse(url=next_path, status_code=303)
            if session is not None:
                return RedirectResponse(url=next_path, status_code=303)
            return create_secure_html_response(
                self._render_login_ui(
                    prefix=prefix,
                    next_path=next_path,
                    notice_title=request.query_params.get("notice") or None,
                    notice_body=request.query_params.get("detail") or None,
                    notice_is_error=request.query_params.get("tone") == "error",
                )
            )

        @self.custom_route(f"{prefix}/login", methods=["POST"])
        async def registry_login(request: Request):
            if not self.auth_enabled:
                payload = {
                    "error": "Registry auth is disabled.",
                    "status": 400,
                }
                if _wants_json(request):
                    return JSONResponse(payload, status_code=400)
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        page_notice_title="Auth disabled",
                        page_notice_body=payload["error"],
                        page_notice_is_error=True,
                    ),
                    status_code=400,
                )

            content_type = request.headers.get("content-type", "")
            expects_json = _wants_json(request)
            try:
                if "application/json" in content_type:
                    raw_payload = await request.json()
                    if not isinstance(raw_payload, dict):
                        raise ValueError("JSON request body must be an object.")
                else:
                    form = await request.form()
                    raw_payload = {str(key): value for key, value in form.items()}
            except Exception as exc:
                message = str(exc) or "Invalid login request."
                if expects_json:
                    return JSONResponse(
                        {"error": message, "status": 400},
                        status_code=400,
                    )
                return create_secure_html_response(
                    self._render_login_ui(
                        prefix=prefix,
                        next_path=_safe_next_path(
                            request.query_params.get("next"),
                            default=prefix,
                        ),
                        notice_title="Login failed",
                        notice_body=message,
                        notice_is_error=True,
                    ),
                    status_code=400,
                )

            username = str(raw_payload.get("username", "")).strip()
            password = str(raw_payload.get("password", ""))
            next_path = _safe_next_path(
                str(raw_payload.get("next", "")),
                default=prefix,
            )
            user = self._auth_settings.authenticate(username, password)
            if user is None:
                payload = {
                    "error": "Invalid username or password.",
                    "status": 401,
                }
                if expects_json:
                    return JSONResponse(payload, status_code=401)
                return create_secure_html_response(
                    self._render_login_ui(
                        prefix=prefix,
                        next_path=next_path,
                        notice_title="Login failed",
                        notice_body=payload["error"],
                        notice_is_error=True,
                    ),
                    status_code=401,
                )

            token = self._auth_settings.issue_token(user)
            session = self._auth_settings.decode_token(token)
            if session is None:
                payload = {
                    "error": "Unable to establish an authenticated session.",
                    "status": 500,
                }
                if expects_json:
                    return JSONResponse(payload, status_code=500)
                return create_secure_html_response(
                    self._render_login_ui(
                        prefix=prefix,
                        next_path=next_path,
                        notice_title="Login failed",
                        notice_body=payload["error"],
                        notice_is_error=True,
                    ),
                    status_code=500,
                )

            if expects_json:
                response = JSONResponse(
                    {
                        "token": token,
                        "session": session.to_dict(),
                    }
                )
            else:
                response = RedirectResponse(url=next_path, status_code=303)
            self._set_auth_cookie(response, token)
            return response

        @self.custom_route(f"{prefix}/logout", methods=["GET"])
        async def registry_logout(request: Request):
            next_path = _safe_next_path(
                request.query_params.get("next"),
                default=prefix,
            )
            response = RedirectResponse(url=next_path, status_code=303)
            self._clear_auth_cookie(response)
            return response

        @self.custom_route(prefix, methods=["GET"])
        async def registry_ui(request: Request):
            session = self._session_from_request(request)
            return create_secure_html_response(
                self._render_registry_ui(
                    prefix=prefix,
                    query=request.query_params.get("q", ""),
                    min_certification=request.query_params.get(
                        "min_certification",
                        "",
                    ),
                    selected_tool=request.query_params.get("tool", ""),
                    session=session,
                    page_notice_title=request.query_params.get("notice") or None,
                    page_notice_body=request.query_params.get("detail") or None,
                    page_notice_is_error=request.query_params.get("tone") == "error",
                )
            )

        @self.custom_route(f"{prefix}/listings/{{tool_name}}", methods=["GET"])
        async def registry_listing_ui(request: Request):
            tool_name = request.path_params.get("tool_name", "")
            session = self._session_from_request(request)
            detail = self.get_verified_tool(tool_name)
            if "error" in detail:
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        query=request.query_params.get("q", ""),
                        min_certification=request.query_params.get(
                            "min_certification",
                            "",
                        ),
                        submission_title="Listing not found",
                        submission_body=detail["error"],
                        submission_is_error=True,
                        session=session,
                    ),
                    status_code=_status_code_from_payload(detail),
                )

            install_payload = self.get_install_recipes(
                tool_name,
                registry_base_url=str(request.base_url).rstrip("/"),
            )
            return create_secure_html_response(
                create_listing_detail_html(
                    server_name=self.name,
                    registry_prefix=prefix,
                    detail=detail,
                    install_recipes=list(install_payload.get("recipes", [])),
                    auth_enabled=self.auth_enabled,
                    session=self._session_payload(session),
                    query=request.query_params.get("q", ""),
                    min_certification=request.query_params.get(
                        "min_certification",
                        "",
                    ),
                )
            )

        @self.custom_route(f"{prefix}/publishers", methods=["GET"])
        async def registry_publishers(request: Request):
            limit = int(request.query_params.get("limit", "200"))
            payload = self.list_publishers(limit=limit)
            if request.query_params.get("view") == "html":
                return create_secure_html_response(
                    create_publisher_index_html(
                        server_name=self.name,
                        registry_prefix=prefix,
                        publishers=payload,
                        auth_enabled=self.auth_enabled,
                        session=self._session_payload(
                            self._session_from_request(request)
                        ),
                    )
                )
            return JSONResponse(payload)

        @self.custom_route(f"{prefix}/publishers/{{publisher_id}}", methods=["GET"])
        async def registry_publisher_profile(request: Request):
            publisher_id = request.path_params.get("publisher_id", "")
            session = self._session_from_request(request)
            payload = self.get_publisher_profile(publisher_id)
            if "error" in payload:
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        submission_title="Publisher not found",
                        submission_body=payload["error"],
                        submission_is_error=True,
                        session=session,
                    ),
                    status_code=_status_code_from_payload(payload),
                )
            return create_secure_html_response(
                create_publisher_profile_html(
                    server_name=self.name,
                    registry_prefix=prefix,
                    profile=payload,
                    auth_enabled=self.auth_enabled,
                    session=self._session_payload(session),
                )
            )

        @self.custom_route(f"{prefix}/review", methods=["GET"])
        async def registry_review_queue(request: Request):
            session = self._session_from_request(request)
            allowed_roles = {RegistryRole.REVIEWER, RegistryRole.ADMIN}
            if self.auth_enabled and session is None:
                return self._login_redirect(request, prefix=prefix)
            if self.auth_enabled and not self._has_roles(session, allowed_roles):
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        session=session,
                        page_notice_title="Access denied",
                        page_notice_body=(
                            "Reviewer or admin role required to access the "
                            "moderation queue."
                        ),
                        page_notice_is_error=True,
                    ),
                    status_code=403,
                )
            return create_secure_html_response(
                self._render_review_queue_ui(
                    prefix=prefix,
                    session=session,
                    notice_title=request.query_params.get("notice") or None,
                    notice_body=request.query_params.get("detail") or None,
                    notice_is_error=request.query_params.get("tone") == "error",
                )
            )

        @self.custom_route(f"{prefix}/review/submissions", methods=["GET"])
        async def registry_review_submissions(request: Request) -> JSONResponse:
            session = self._session_from_request(request)
            if self.auth_enabled and session is None:
                return JSONResponse(
                    {"error": "Authentication required.", "status": 401},
                    status_code=401,
                )
            if self.auth_enabled and not self._has_roles(
                session,
                {RegistryRole.REVIEWER, RegistryRole.ADMIN},
            ):
                return JSONResponse(
                    {"error": "Reviewer or admin role required.", "status": 403},
                    status_code=403,
                )
            return JSONResponse(
                self._filter_queue_for_session(
                    self.get_moderation_queue(),
                    session,
                )
            )

        @self.custom_route(
            f"{prefix}/review/{{listing_id}}/{{action_name}}",
            methods=["POST"],
        )
        async def registry_review_action(request: Request):
            listing_id = request.path_params.get("listing_id", "")
            action_name = request.path_params.get("action_name", "")
            session = self._session_from_request(request)
            content_type = request.headers.get("content-type", "")
            expects_json = "application/json" in content_type or (
                "application/json" in request.headers.get("accept", "")
            )
            required_roles = self._moderation_roles_for_action(action_name)

            if self.auth_enabled and session is None:
                if expects_json:
                    return JSONResponse(
                        {"error": "Authentication required.", "status": 401},
                        status_code=401,
                    )
                return self._login_redirect(request, prefix=prefix)

            if (
                required_roles
                and self.auth_enabled
                and not self._has_roles(
                    session,
                    required_roles,
                )
            ):
                message = (
                    "Admin role required."
                    if required_roles == {RegistryRole.ADMIN}
                    else "Reviewer or admin role required."
                )
                if expects_json:
                    return JSONResponse(
                        {"error": message, "status": 403},
                        status_code=403,
                    )
                return create_secure_html_response(
                    self._render_review_queue_ui(
                        prefix=prefix,
                        session=session,
                        notice_title="Moderation failed",
                        notice_body=message,
                        notice_is_error=True,
                    ),
                    status_code=403,
                )

            try:
                if "application/json" in content_type:
                    raw_payload = await request.json()
                    if not isinstance(raw_payload, dict):
                        raise ValueError("JSON request body must be an object.")
                else:
                    form = await request.form()
                    raw_payload = {str(key): value for key, value in form.items()}
            except Exception as exc:
                message = str(exc) or "Invalid moderation request."
                if expects_json:
                    return JSONResponse(
                        {"error": message, "status": 400},
                        status_code=400,
                    )
                return create_secure_html_response(
                    self._render_review_queue_ui(
                        prefix=prefix,
                        notice_title="Moderation failed",
                        notice_body=message,
                        notice_is_error=True,
                    ),
                    status_code=400,
                )

            metadata: dict[str, Any] | None = None
            raw_metadata = raw_payload.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
            elif isinstance(raw_metadata, str) and raw_metadata.strip():
                try:
                    parsed = json.loads(raw_metadata)
                except json.JSONDecodeError as exc:
                    if expects_json:
                        return JSONResponse(
                            {
                                "error": f"Invalid metadata JSON: {exc}",
                                "status": 400,
                            },
                            status_code=400,
                        )
                    return create_secure_html_response(
                        self._render_review_queue_ui(
                            prefix=prefix,
                            notice_title="Moderation failed",
                            notice_body=f"Invalid metadata JSON: {exc}",
                            notice_is_error=True,
                        ),
                        status_code=400,
                    )
                if not isinstance(parsed, dict):
                    message = "`metadata` must decode to an object."
                    if expects_json:
                        return JSONResponse(
                            {"error": message, "status": 400},
                            status_code=400,
                        )
                    return create_secure_html_response(
                        self._render_review_queue_ui(
                            prefix=prefix,
                            notice_title="Moderation failed",
                            notice_body=message,
                            notice_is_error=True,
                        ),
                        status_code=400,
                    )
                metadata = parsed

            payload = self.moderate_listing(
                listing_id,
                action_name=action_name,
                moderator_id=str(raw_payload.get("moderator_id", "purecipher-admin")),
                reason=str(raw_payload.get("reason", "")),
                metadata=metadata,
            )
            status_code = _status_code_from_payload(payload)
            if expects_json:
                return JSONResponse(payload, status_code=status_code)

            if "error" in payload:
                return create_secure_html_response(
                    self._render_review_queue_ui(
                        prefix=prefix,
                        notice_title="Moderation failed",
                        notice_body=payload["error"],
                        notice_is_error=True,
                    ),
                    status_code=status_code,
                )

            redirect_params = urlencode(
                {
                    "notice": "Moderation updated",
                    "detail": (
                        f"{payload['listing']['display_name']} -> "
                        f"{payload['listing']['status']}"
                    ),
                    "tone": "success",
                }
            )
            return RedirectResponse(
                url=f"{prefix}/review?{redirect_params}",
                status_code=303,
            )

        @self.custom_route(prefix, methods=["POST"])
        async def registry_ui_submit(request: Request):
            session = self._session_from_request(request)
            form = await request.form()
            manifest_text = str(form.get("manifest", SAMPLE_MANIFEST_JSON))
            display_name = str(form.get("display_name", ""))
            categories = str(form.get("categories", ""))
            requested_level = str(
                form.get("requested_level", self.minimum_certification.value)
            )

            if self.auth_enabled and not self._has_roles(
                session,
                {
                    RegistryRole.PUBLISHER,
                    RegistryRole.REVIEWER,
                    RegistryRole.ADMIN,
                },
            ):
                status_code = 401 if session is None else 403
                message = (
                    "Sign in with a publisher, reviewer, or admin account "
                    "before submitting listings."
                )
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        manifest_text=manifest_text,
                        display_name=display_name,
                        categories=categories,
                        requested_level=requested_level,
                        session=session,
                        page_notice_title="Submission blocked",
                        page_notice_body=message,
                        page_notice_is_error=True,
                    ),
                    status_code=status_code,
                )

            try:
                manifest_data = json.loads(manifest_text)
                if not isinstance(manifest_data, dict):
                    raise ValueError("`manifest` must be a JSON object.")

                manifest = _parse_manifest(manifest_data)
                result = self.submit_tool(
                    manifest,
                    display_name=display_name,
                    categories=_coerce_categories(
                        [
                            value.strip()
                            for value in categories.split(",")
                            if value.strip()
                        ]
                    )
                    or None,
                    requested_level=_coerce_level(
                        requested_level,
                        default=self.minimum_certification,
                    ),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                return create_secure_html_response(
                    self._render_registry_ui(
                        prefix=prefix,
                        manifest_text=manifest_text,
                        display_name=display_name,
                        categories=categories,
                        requested_level=requested_level,
                        submission_title="Submission failed",
                        submission_body=str(exc),
                        submission_is_error=True,
                        session=session,
                    ),
                    status_code=400,
                )

            selected_tool = (
                result.listing.tool_name if result.listing is not None else ""
            )
            return create_secure_html_response(
                self._render_registry_ui(
                    prefix=prefix,
                    selected_tool=selected_tool,
                    manifest_text=manifest_text,
                    display_name=display_name,
                    categories=categories,
                    requested_level=requested_level,
                    session=session,
                    submission_title=result.reason,
                    submission_body=(
                        f"Listing: {selected_tool or 'n/a'} · "
                        f"Certification: {result.attestation.certification_level.value}"
                    ),
                    submission_is_error=not result.accepted,
                ),
                status_code=200 if result.accepted else 400,
            )

        @self.custom_route(f"{prefix}/tools", methods=["GET"])
        async def registry_tools(request: Request) -> JSONResponse:
            tags = set(_split_multi_value(request, "tag")) or None
            categories = (
                _coerce_categories(_split_multi_value(request, "category")) or None
            )
            limit = int(request.query_params.get("limit", "50"))
            level = _coerce_level(
                request.query_params.get("min_certification"),
                default=self.minimum_certification,
            )
            payload = self.list_verified_tools(
                query=request.query_params.get("q"),
                author=request.query_params.get("author"),
                tags=tags,
                categories=categories,
                min_certification=level,
                limit=limit,
            )
            return JSONResponse(payload)

        @self.custom_route(f"{prefix}/tools/{{tool_name}}", methods=["GET"])
        async def registry_tool_detail(request: Request) -> JSONResponse:
            tool_name = request.path_params.get("tool_name", "")
            payload = self.get_verified_tool(tool_name)
            return JSONResponse(payload, status_code=_status_code_from_payload(payload))

        @self.custom_route(f"{prefix}/install/{{tool_name}}", methods=["GET"])
        async def registry_install_recipes(request: Request) -> JSONResponse:
            tool_name = request.path_params.get("tool_name", "")
            payload = self.get_install_recipes(
                tool_name,
                registry_base_url=str(request.base_url).rstrip("/"),
            )
            return JSONResponse(payload, status_code=_status_code_from_payload(payload))

        @self.custom_route(f"{prefix}/submit", methods=["POST"])
        async def registry_submit(request: Request) -> JSONResponse:
            session = self._session_from_request(request)
            if self.auth_enabled and not self._has_roles(
                session,
                {
                    RegistryRole.PUBLISHER,
                    RegistryRole.REVIEWER,
                    RegistryRole.ADMIN,
                },
            ):
                return JSONResponse(
                    {
                        "error": (
                            "Publisher, reviewer, or admin role required "
                            "to submit listings."
                        ),
                        "status": 401 if session is None else 403,
                    },
                    status_code=401 if session is None else 403,
                )
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"error": "Invalid JSON body", "status": 400},
                    status_code=400,
                )

            manifest_data = body.get("manifest")
            if not isinstance(manifest_data, dict):
                return JSONResponse(
                    {"error": "`manifest` must be an object", "status": 400},
                    status_code=400,
                )

            try:
                manifest = _parse_manifest(manifest_data)
                result = self.submit_tool(
                    manifest,
                    display_name=body.get("display_name", ""),
                    description=body.get("description", ""),
                    categories=_coerce_categories(body.get("categories")),
                    homepage_url=body.get("homepage_url", ""),
                    source_url=body.get("source_url", ""),
                    tool_license=body.get("tool_license", ""),
                    tags=set(body.get("tags", [])) or None,
                    metadata=dict(body.get("metadata", {})),
                    changelog=body.get("changelog", ""),
                    requested_level=_coerce_level(body.get("requested_level")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                return JSONResponse(
                    {"error": str(exc), "status": 400},
                    status_code=400,
                )

            payload = result.to_dict()
            return JSONResponse(
                payload,
                status_code=201 if result.accepted else 400,
            )

        @self.custom_route(f"{prefix}/verify", methods=["POST"])
        async def registry_verify(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"error": "Invalid JSON body", "status": 400},
                    status_code=400,
                )

            tool_name = body.get("tool_name", "")
            if not tool_name:
                return JSONResponse(
                    {"error": "`tool_name` is required", "status": 400},
                    status_code=400,
                )

            manifest = None
            manifest_data = body.get("manifest")
            if manifest_data is not None:
                if not isinstance(manifest_data, dict):
                    return JSONResponse(
                        {"error": "`manifest` must be an object", "status": 400},
                        status_code=400,
                    )
                try:
                    manifest = _parse_manifest(manifest_data)
                except (KeyError, TypeError, ValueError) as exc:
                    return JSONResponse(
                        {"error": str(exc), "status": 400},
                        status_code=400,
                    )

            payload = self.verify_tool(tool_name, manifest=manifest)
            return JSONResponse(payload, status_code=_status_code_from_payload(payload))

        self._registry_api_mounted = True
        self._registry_prefix = prefix
        return self
