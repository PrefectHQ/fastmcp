from __future__ import annotations

import json
from typing import Any, cast

from starlette.testclient import TestClient

from fastmcp.server.security.certification.attestation import CertificationLevel
from fastmcp.server.security.certification.manifest import (
    DataClassification,
    DataFlowDeclaration,
    PermissionScope,
    ResourceAccessDeclaration,
    SecurityManifest,
)
from purecipher import PureCipherRegistry, ToolCategory
from purecipher.auth import RegistryAuthSettings

TEST_SIGNING_SECRET = "purecipher-registry-signing-secret-for-tests"
TEST_JWT_SECRET = "purecipher-registry-jwt-secret-for-tests"


def _manifest(**overrides: Any) -> SecurityManifest:
    defaults: dict[str, Any] = dict(
        tool_name="weather-lookup",
        version="1.0.0",
        author="acme",
        description="Fetch current weather for a city.",
        permissions={PermissionScope.NETWORK_ACCESS},
        data_flows=[
            DataFlowDeclaration(
                source="input.city",
                destination="output.forecast",
                classification=DataClassification.PUBLIC,
                description="City name is sent to the weather provider.",
            )
        ],
        resource_access=[
            ResourceAccessDeclaration(
                resource_pattern="https://api.weather.example/*",
                access_type="read",
                description="Call weather provider endpoint.",
                classification=DataClassification.PUBLIC,
            )
        ],
        tags={"weather", "api"},
    )
    defaults.update(overrides)
    return SecurityManifest(**cast(Any, defaults))


def _runtime_metadata() -> dict[str, object]:
    return {
        "endpoint": "https://mcp.acme.example/weather",
        "transport": "streamable-http",
        "command": "uvx",
        "args": ["weather-lookup"],
        "docker_image": "ghcr.io/acme/weather-lookup:1.0.0",
        "env": {"WEATHER_API_KEY": "${WEATHER_API_KEY}"},
    }


def _auth_settings() -> RegistryAuthSettings:
    return RegistryAuthSettings.from_values(
        enabled=True,
        issuer="purecipher-registry",
        jwt_secret=TEST_JWT_SECRET,
    )


class TestPureCipherRegistry:
    def test_constructor_mounts_registry_routes(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
        )

        paths = {
            path
            for route in registry._additional_http_routes
            if (path := getattr(route, "path", None)) is not None
        }
        assert "/registry" in paths
        assert "/registry/app" in paths
        assert "/registry/health" in paths
        assert "/registry/session" in paths
        assert "/registry/login" in paths
        assert "/registry/logout" in paths
        assert "/registry/publish" in paths
        assert "/registry/listings/{tool_name}" in paths
        assert "/registry/install/{tool_name}" in paths
        assert "/registry/publishers" in paths
        assert "/registry/publishers/{publisher_id}" in paths
        assert "/registry/review" in paths
        assert "/registry/review/submissions" in paths
        assert "/registry/review/{listing_id}/{action_name}" in paths
        assert "/registry/policy" in paths
        assert "/registry/policy/schema" in paths
        assert "/registry/policy/versions" in paths
        assert "/registry/policy/versions/diff" in paths
        assert "/registry/policy/versions/rollback" in paths
        assert "/registry/policy/proposals" in paths
        assert "/registry/policy/proposals/{proposal_id}" in paths
        assert "/registry/policy/proposals/{proposal_id}/approve" in paths
        assert "/registry/policy/proposals/{proposal_id}/assign" in paths
        assert "/registry/policy/proposals/{proposal_id}/simulate" in paths
        assert "/registry/policy/proposals/{proposal_id}/deploy" in paths
        assert "/registry/policy/proposals/{proposal_id}/reject" in paths
        assert "/registry/policy/proposals/{proposal_id}/withdraw" in paths
        assert "/registry/policy/providers" in paths
        assert "/registry/policy/providers/{index}" in paths
        assert "/registry/tools" in paths
        assert "/registry/submit" in paths
        assert "/registry/preflight" in paths

    def test_submit_tool_accepts_certified_manifest(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)

        result = registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            tool_license="MIT",
            requested_level=CertificationLevel.BASIC,
        )

        assert result.accepted is True
        assert result.listing is not None
        assert result.listing.tool_name == "weather-lookup"

        detail = registry.get_verified_tool("weather-lookup")
        assert detail["tool_name"] == "weather-lookup"
        assert detail["verification"]["valid"] is True

    def test_submit_tool_rejects_below_minimum_level(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)

        result = registry.submit_tool(
            _manifest(),
            requested_level=CertificationLevel.SELF_ATTESTED,
        )

        assert result.accepted is False
        assert "minimum certification level" in result.reason

    def test_list_verified_tools_returns_catalog(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )

        catalog = registry.list_verified_tools(query="weather")

        assert catalog["count"] == 1
        assert catalog["tools"][0]["tool_name"] == "weather-lookup"

    def test_persistence_reloads_verified_tools(self, tmp_path):
        db_path = tmp_path / "purecipher-registry.db"

        registry1 = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            persistence_path=str(db_path),
        )
        result = registry1.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        assert result.accepted is True

        registry2 = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            persistence_path=str(db_path),
        )
        catalog = registry2.list_verified_tools(query="weather")
        detail = registry2.get_verified_tool("weather-lookup")

        assert catalog["count"] == 1
        assert catalog["tools"][0]["tool_name"] == "weather-lookup"
        assert detail["attestation"]["tool_name"] == "weather-lookup"
        assert detail["trust_score"] is not None
        assert detail["verification"]["valid"] is True

    def test_http_registry_submit_and_verify(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            submit = client.post(
                "/registry/submit",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network", "utility"],
                    "tool_license": "MIT",
                    "requested_level": "basic",
                },
            )

            assert submit.status_code == 201
            assert submit.json()["accepted"] is True

            listing = client.get("/registry/tools/weather-lookup")
            assert listing.status_code == 200
            assert listing.json()["tool_name"] == "weather-lookup"

            verify = client.post(
                "/registry/verify",
                json={"tool_name": "weather-lookup"},
            )
            assert verify.status_code == 200
            assert verify.json()["verification"]["valid"] is True

    def test_http_registry_install_recipes(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            source_url="https://github.com/acme/weather-lookup",
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.get("/registry/install/weather-lookup")

            assert response.status_code == 200
            payload = response.json()
            assert payload["tool_name"] == "weather-lookup"
            recipe_ids = {recipe["recipe_id"] for recipe in payload["recipes"]}
            assert {
                "registry_reference",
                "mcp_client_http",
                "mcp_client_stdio",
                "docker_compose",
                "verify_attestation",
            } <= recipe_ids
            assert any(
                "ghcr.io/acme/weather-lookup:1.0.0" in recipe["content"]
                for recipe in payload["recipes"]
            )

    def test_http_registry_listing_detail_page(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            source_url="https://github.com/acme/weather-lookup",
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.get(
                "/registry/listings/weather-lookup?q=weather&min_certification=basic"
            )

            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "Get oriented quickly" in response.text
            assert "Fastest start" in response.text
            assert "Access and setup snapshot" in response.text
            assert "Ways To Use This Tool" in response.text
            assert "Why People Choose It" in response.text
            assert "Start Here" in response.text
            assert "/registry/install/weather-lookup" in response.text
            assert "Connect From Another App" in response.text
            assert "Copy all setup steps" in response.text
            assert "Copy tool JSON" in response.text
            assert "Back to browse" in response.text
            assert ">Publisher<" in response.text

    def test_http_registry_publisher_page(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )
        registry.submit_tool(
            _manifest(
                tool_name="forecast-archive",
                version="2.1.0",
                description="Historical weather and climate data.",
                tags={"weather", "history"},
            ),
            display_name="Forecast Archive",
            categories={ToolCategory.DATA_ACCESS},
            requested_level=CertificationLevel.BASIC,
        )
        registry.submit_tool(
            _manifest(
                tool_name="news-wire",
                author="another-publisher",
                description="Breaking news API integration.",
                tags={"news"},
            ),
            display_name="News Wire",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            index = client.get("/registry/publishers")
            assert index.status_code == 200
            summaries = index.json()
            assert summaries["count"] == 2
            assert any(
                publisher["publisher_id"] == "acme"
                for publisher in summaries["publishers"]
            )

            response = client.get("/registry/publishers/acme")

            assert response.status_code == 200
            assert "Publisher Profile" in response.text
            assert "Start with this publisher" in response.text
            assert "Best first click" in response.text
            assert "Trust snapshot" in response.text
            assert "Weather Lookup" in response.text
            assert "Forecast Archive" in response.text
            assert "Live Tools" in response.text

            directory = client.get("/registry/publishers?view=html")
            assert directory.status_code == 200
            assert "People and teams behind the tools" in directory.text
            assert "Featured Publishers" in directory.text

    def test_http_registry_review_queue_and_actions(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            require_moderation=True,
        )
        result = registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )
        assert result.accepted is True
        assert result.listing is not None
        assert result.listing.status.value == "pending_review"

        app = registry.http_app()

        with TestClient(app) as client:
            public_catalog = client.get("/registry/tools")
            assert public_catalog.status_code == 200
            assert public_catalog.json()["count"] == 0

            queue = client.get("/registry/review/submissions")
            assert queue.status_code == 200
            queue_payload = queue.json()
            assert queue_payload["counts"]["pending_review"] == 1
            assert (
                queue_payload["sections"]["pending_review"][0]["tool_name"]
                == "weather-lookup"
            )

            review_page = client.get("/registry/review")
            assert review_page.status_code == 200
            assert "Review shared tools" in review_page.text
            assert "Waiting For Approval" in review_page.text

            approve = client.post(
                f"/registry/review/{result.listing.listing_id}/approve",
                json={
                    "moderator_id": "moderator-1",
                    "reason": "Manifest is ready for publication.",
                },
            )
            assert approve.status_code == 200
            approve_payload = approve.json()
            assert approve_payload["listing"]["status"] == "published"
            assert approve_payload["decision"]["moderator_id"] == "moderator-1"

            published_catalog = client.get("/registry/tools")
            assert published_catalog.status_code == 200
            assert published_catalog.json()["count"] == 1

            suspend = client.post(
                f"/registry/review/{result.listing.listing_id}/suspend",
                json={
                    "moderator_id": "moderator-2",
                    "reason": "Temporarily disabled for investigation.",
                },
            )
            assert suspend.status_code == 200
            assert suspend.json()["listing"]["status"] == "suspended"

            unsuspend = client.post(
                f"/registry/review/{result.listing.listing_id}/unsuspend",
                json={
                    "moderator_id": "moderator-3",
                    "reason": "Issue resolved.",
                },
            )
            assert unsuspend.status_code == 200
            assert unsuspend.json()["listing"]["status"] == "published"

    def test_http_registry_public_routes_only_expose_published_listings(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            require_moderation=True,
        )
        result = registry.submit_tool(
            _manifest(tool_name="pending-tool"),
            display_name="Pending Tool",
            categories={ToolCategory.NETWORK},
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )
        assert result.accepted is True
        assert result.listing is not None

        app = registry.http_app()

        with TestClient(app) as client:
            pending_detail = client.get("/registry/tools/pending-tool")
            pending_install = client.get("/registry/install/pending-tool")
            pending_listing = client.get("/registry/listings/pending-tool")
            pending_verify = client.post(
                "/registry/verify",
                json={"tool_name": "pending-tool"},
            )

            assert pending_detail.status_code == 404
            assert pending_install.status_code == 404
            assert pending_listing.status_code == 404
            assert pending_verify.status_code == 404

            approve = client.post(
                f"/registry/review/{result.listing.listing_id}/approve",
                json={"moderator_id": "reviewer", "reason": "Looks good."},
            )
            assert approve.status_code == 200

            published_detail = client.get("/registry/tools/pending-tool")
            assert published_detail.status_code == 200
            assert published_detail.json()["status"] == "published"

            suspend = client.post(
                f"/registry/review/{result.listing.listing_id}/suspend",
                json={"moderator_id": "admin", "reason": "Temporarily paused."},
            )
            assert suspend.status_code == 200

            suspended_detail = client.get("/registry/tools/pending-tool")
            suspended_install = client.get("/registry/install/pending-tool")

            assert suspended_detail.status_code == 404
            assert suspended_install.status_code == 404

    def test_submit_tool_requeues_existing_listing_when_moderation_is_enabled(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            require_moderation=True,
        )
        first = registry.submit_tool(
            _manifest(tool_name="weather-lookup", version="1.0.0"),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )

        assert first.accepted is True
        assert first.listing is not None
        assert first.listing.status.value == "pending_review"

        approved = registry.moderate_listing(
            first.listing.listing_id,
            action_name="approve",
            moderator_id="reviewer-1",
            reason="Initial version approved.",
        )
        assert approved["listing"]["status"] == "published"

        second = registry.submit_tool(
            _manifest(tool_name="weather-lookup", version="1.1.0"),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            metadata=_runtime_metadata(),
            requested_level=CertificationLevel.BASIC,
        )

        assert second.accepted is True
        assert second.listing is not None
        assert second.listing.status.value == "pending_review"

        detail = registry.get_verified_tool("weather-lookup")
        assert detail["status"] == 404

    def test_http_registry_ui_route(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            # Auth is disabled by default, so /registry should redirect to the app UI.
            landing = client.get("/registry", follow_redirects=False)
            assert landing.status_code == 303
            assert landing.headers["location"] == "/registry/app"

            response = client.get("/registry/app")

            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "PureCipher Secured MCP Registry" in response.text
            assert "Browse Tools" in response.text
            assert "What would you like to do?" in response.text
            assert "Find a tool you can trust" in response.text
            assert "Start Here" in response.text
            assert "Choose your next step" in response.text
            assert "Popular topics" in response.text
            assert "Overview" in response.text
            assert "Featured Publishers" in response.text
            assert "Share A Tool" in response.text

    def test_http_registry_policy_proposal_lifecycle(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            created = client.post(
                "/registry/policy/proposals",
                json={
                    "action": "add",
                    "config": {
                        "type": "denylist",
                        "policy_id": "admin-guard",
                        "version": "1.0.0",
                        "denied": ["admin-*"],
                    },
                    "description": "Protect admin tools.",
                },
            )
            assert created.status_code == 200
            proposal = created.json()["proposal"]
            proposal_id = proposal["proposal_id"]
            assert proposal["status"] == "validated"

            proposals = client.get("/registry/policy/proposals")
            assert proposals.status_code == 200
            assert proposals.json()["pending_count"] == 1

            assigned = client.post(
                f"/registry/policy/proposals/{proposal_id}/assign",
                json={
                    "reviewer": "reviewer",
                    "note": "Reviewer owns the rollout.",
                },
            )
            assert assigned.status_code == 200
            assert assigned.json()["proposal"]["assigned_reviewer"] == "reviewer"

            simulated = client.post(
                f"/registry/policy/proposals/{proposal_id}/simulate"
            )
            assert simulated.status_code == 200
            assert simulated.json()["proposal"]["status"] == "simulated"
            assert simulated.json()["simulation"]["total"] == 5

            approved = client.post(
                f"/registry/policy/proposals/{proposal_id}/approve",
                json={"note": "Ready to release."},
            )
            assert approved.status_code == 200
            assert approved.json()["proposal"]["status"] == "approved"

            deployed = client.post(
                f"/registry/policy/proposals/{proposal_id}/deploy",
                json={"note": "Applying to the live chain."},
            )
            assert deployed.status_code == 200
            assert deployed.json()["proposal"]["status"] == "deployed"
            assert deployed.json()["policy"]["provider_count"] == 2
            trail = deployed.json()["proposal"]["decision_trail"]
            events = [item["event"] for item in trail]
            assert "assigned" in events
            assert events[-3:] == ["simulated", "approved", "deployed"]

    def test_http_registry_rejects_stale_policy_proposal(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            original = client.post(
                "/registry/policy/proposals",
                json={
                    "action": "add",
                    "config": {
                        "type": "allowlist",
                        "policy_id": "tool-gate",
                        "version": "1.0.0",
                        "allowed": ["tool:*"],
                    },
                    "description": "Gate tool access.",
                },
            )
            assert original.status_code == 200
            original_proposal = original.json()["proposal"]
            original_id = original_proposal["proposal_id"]
            assert original_proposal["base_version_number"] == 1
            assert original_proposal["live_version_number"] == 1
            assert original_proposal["is_stale"] is False

            live = client.post(
                "/registry/policy/proposals",
                json={
                    "action": "add",
                    "config": {
                        "type": "denylist",
                        "policy_id": "admin-guard",
                        "version": "1.0.0",
                        "denied": ["admin-*"],
                    },
                    "description": "Protect admin tools.",
                },
            )
            assert live.status_code == 200
            live_id = live.json()["proposal"]["proposal_id"]

            simulate_live = client.post(
                f"/registry/policy/proposals/{live_id}/simulate"
            )
            assert simulate_live.status_code == 200
            approve_live = client.post(f"/registry/policy/proposals/{live_id}/approve")
            assert approve_live.status_code == 200
            deploy_live = client.post(f"/registry/policy/proposals/{live_id}/deploy")
            assert deploy_live.status_code == 200

            refreshed = client.get(f"/registry/policy/proposals/{original_id}")
            assert refreshed.status_code == 200
            assert refreshed.json()["live_version_number"] == 2
            assert refreshed.json()["is_stale"] is True

            stale_simulate = client.post(
                f"/registry/policy/proposals/{original_id}/simulate"
            )
            assert stale_simulate.status_code == 400
            assert "Create a fresh proposal" in stale_simulate.json()["error"]

    def test_http_registry_ui_hides_review_details_from_public_users(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
            require_moderation=True,
        )
        registry.submit_tool(
            _manifest(tool_name="pending-tool", version="1.0.0"),
            display_name="Pending Tool",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.get("/registry", follow_redirects=True)

            assert response.status_code == 200
            assert "PureCipher Secured MCP Registry" in response.text
            assert "Pending Tool" not in response.text
            assert 'href="/registry/review"' not in response.text

    def test_http_registry_ui_shows_review_details_to_reviewers(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
            require_moderation=True,
        )
        registry.submit_tool(
            _manifest(tool_name="pending-tool", version="1.0.0"),
            display_name="Pending Tool",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            login = client.post(
                "/registry/login",
                json={"username": "reviewer", "password": "reviewer123"},
            )
            assert login.status_code == 200

            response = client.get("/registry/app")

            assert response.status_code == 200
            assert "Pending Tool" in response.text
            assert 'href="/registry/review"' in response.text
            assert "Open approvals" in response.text

    def test_registry_pages_do_not_link_to_post_only_api_routes(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            catalog = client.get("/registry")
            publish = client.get("/registry/publish")

            assert 'href="/registry/submit"' not in catalog.text
            assert 'href="/registry/preflight"' not in catalog.text
            assert 'href="/registry/submit"' not in publish.text
            assert 'href="/registry/preflight"' not in publish.text
            assert "POST /registry/submit" in catalog.text
            assert "POST /registry/preflight" in publish.text

    def test_http_registry_ui_form_submission(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.post(
                "/registry/publish",
                data={
                    "manifest": json.dumps(_manifest().to_dict(), indent=2),
                    "runtime_metadata": json.dumps(_runtime_metadata(), indent=2),
                    "display_name": "Weather Lookup",
                    "categories": "network,utility",
                    "tags": "weather,api",
                    "source_url": "https://github.com/acme/weather-lookup",
                    "tool_license": "MIT",
                    "requested_level": "basic",
                    "submission_action": "publish",
                },
            )

            assert response.status_code == 200
            assert "Accepted into the PureCipher verified registry." in response.text
            assert "Share Form" in response.text
            assert "Check Results" in response.text
            assert "Ready To Share" in response.text

    def test_http_registry_publish_page_and_preflight(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            page = client.get("/registry/publish")

            assert page.status_code == 200
            assert "Share your tool" in page.text
            assert "Share your tool without guessing" in page.text
            assert "What happens on this page?" in page.text
            assert "Sharing should feel straightforward" in page.text
            assert "Share Form" in page.text
            assert "Check Results" in page.text
            assert "Choose a starting point" in page.text
            assert "Use this starting point" in page.text

            preview = client.post(
                "/registry/publish",
                data={
                    "manifest": json.dumps(_manifest().to_dict(), indent=2),
                    "runtime_metadata": json.dumps(_runtime_metadata(), indent=2),
                    "display_name": "Weather Lookup",
                    "categories": "network,utility",
                    "tags": "weather,api",
                    "source_url": "https://github.com/acme/weather-lookup",
                    "tool_license": "MIT",
                    "requested_level": "basic",
                    "submission_action": "preview",
                },
            )

            assert preview.status_code == 200
            assert "Preflight complete" in preview.text
            assert "What People Will See" in preview.text
            assert "Things To Fix" in preview.text

    def test_http_registry_preflight_api(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.post(
                "/registry/preflight",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network", "utility"],
                    "requested_level": "basic",
                    "metadata": _runtime_metadata(),
                },
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["ready_for_publish"] is True
            assert payload["install_ready"] is True
            recipe_ids = {recipe["recipe_id"] for recipe in payload["install_recipes"]}
            assert "mcp_client_http" in recipe_ids
            assert "docker_compose" in recipe_ids

    def test_http_registry_tools_search(self):
        registry = PureCipherRegistry(signing_secret=TEST_SIGNING_SECRET)
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.get("/registry/tools?q=weather&category=network")

            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 1
            assert payload["tools"][0]["tool_name"] == "weather-lookup"
