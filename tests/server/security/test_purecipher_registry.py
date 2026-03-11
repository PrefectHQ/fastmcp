from __future__ import annotations

import json

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


def _manifest(**overrides) -> SecurityManifest:
    defaults = dict(
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
    return SecurityManifest(**defaults)


def _runtime_metadata() -> dict[str, object]:
    return {
        "endpoint": "https://mcp.acme.example/weather",
        "transport": "streamable-http",
        "command": "uvx",
        "args": ["weather-lookup"],
        "docker_image": "ghcr.io/acme/weather-lookup:1.0.0",
        "env": {"WEATHER_API_KEY": "${WEATHER_API_KEY}"},
    }


class TestPureCipherRegistry:
    def test_constructor_mounts_registry_routes(self):
        registry = PureCipherRegistry(
            signing_secret="test-secret",
        )

        paths = {route.path for route in registry._additional_http_routes}
        assert "/registry" in paths
        assert "/registry/health" in paths
        assert "/registry/session" in paths
        assert "/registry/login" in paths
        assert "/registry/logout" in paths
        assert "/registry/listings/{tool_name}" in paths
        assert "/registry/install/{tool_name}" in paths
        assert "/registry/publishers" in paths
        assert "/registry/publishers/{publisher_id}" in paths
        assert "/registry/review" in paths
        assert "/registry/review/submissions" in paths
        assert "/registry/review/{listing_id}/{action_name}" in paths
        assert "/registry/tools" in paths
        assert "/registry/submit" in paths

    def test_submit_tool_accepts_certified_manifest(self):
        registry = PureCipherRegistry(signing_secret="test-secret")

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
        registry = PureCipherRegistry(signing_secret="test-secret")

        result = registry.submit_tool(
            _manifest(),
            requested_level=CertificationLevel.SELF_ATTESTED,
        )

        assert result.accepted is False
        assert "minimum certification level" in result.reason

    def test_list_verified_tools_returns_catalog(self):
        registry = PureCipherRegistry(signing_secret="test-secret")
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
            signing_secret="test-secret",
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
            signing_secret="test-secret",
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
        registry = PureCipherRegistry(signing_secret="test-secret")
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
        registry = PureCipherRegistry(signing_secret="test-secret")
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
        registry = PureCipherRegistry(signing_secret="test-secret")
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
            assert "Install Recipes" in response.text
            assert "/registry/install/weather-lookup" in response.text
            assert "MCP Client Config (HTTP)" in response.text
            assert "Back to catalog" in response.text
            assert "Publisher profile" in response.text

    def test_http_registry_publisher_page(self):
        registry = PureCipherRegistry(signing_secret="test-secret")
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
            assert "Weather Lookup" in response.text
            assert "Forecast Archive" in response.text
            assert "Published Listings" in response.text

            directory = client.get("/registry/publishers?view=html")
            assert directory.status_code == 200
            assert "Registry Publishers" in directory.text
            assert "Featured Publisher Set" in directory.text

    def test_http_registry_review_queue_and_actions(self):
        registry = PureCipherRegistry(
            signing_secret="test-secret",
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
            assert "Registry Review Queue" in review_page.text
            assert "Pending Review" in review_page.text

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

    def test_http_registry_ui_route(self):
        registry = PureCipherRegistry(signing_secret="test-secret")
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.get("/registry")

            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "PureCipher Secured MCP Registry" in response.text
            assert "/registry/tools" in response.text
            assert "Verified Catalog" in response.text
            assert "Registry Dashboard" in response.text
            assert "Featured Publishers" in response.text
            assert "Submit Manifest" in response.text

    def test_http_registry_ui_form_submission(self):
        registry = PureCipherRegistry(signing_secret="test-secret")
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.post(
                "/registry",
                data={
                    "manifest": json.dumps(_manifest().to_dict(), indent=2),
                    "display_name": "Weather Lookup",
                    "categories": "network,utility",
                    "requested_level": "basic",
                },
            )

            assert response.status_code == 200
            assert "Accepted into the PureCipher verified registry." in response.text
            assert "weather-lookup" in response.text
            assert "Open full listing" in response.text

    def test_http_registry_tools_search(self):
        registry = PureCipherRegistry(signing_secret="test-secret")
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
