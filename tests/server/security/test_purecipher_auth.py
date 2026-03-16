from __future__ import annotations

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
from purecipher import PureCipherRegistry, RegistryAuthSettings, ToolCategory

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


def _auth_settings() -> RegistryAuthSettings:
    return RegistryAuthSettings.from_values(
        enabled=True,
        issuer="purecipher-registry",
        jwt_secret=TEST_JWT_SECRET,
        users_json="",
    )


class TestPureCipherRegistryAuth:
    def test_login_session_and_logout_round_trip(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            login_page = client.get("/registry/login?next=/registry/review")

            assert login_page.status_code == 200
            assert "PureCipher Secured MCP Registry" in login_page.text
            assert "Sign In" in login_page.text

            login = client.post(
                "/registry/login",
                data={
                    "username": "reviewer",
                    "password": "reviewer123",
                    "next": "/registry/review",
                },
                follow_redirects=False,
            )

            assert login.status_code == 303
            assert login.headers["location"] == "/registry/review"

            session = client.get("/registry/session")
            assert session.status_code == 200
            session_payload = session.json()
            assert session_payload["auth_enabled"] is True
            assert session_payload["session"]["role"] == "reviewer"
            assert session_payload["session"]["can_review"] is True

            logout = client.get("/registry/logout", follow_redirects=False)
            assert logout.status_code == 303

            cleared = client.get("/registry/session")
            assert cleared.status_code == 200
            assert cleared.json()["session"] is None

    def test_review_routes_require_auth_when_enabled(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
            require_moderation=True,
        )
        registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK},
            requested_level=CertificationLevel.BASIC,
        )
        app = registry.http_app()

        with TestClient(app) as client:
            review_page = client.get("/registry/review", follow_redirects=False)
            assert review_page.status_code == 303
            assert review_page.headers["location"].startswith("/registry/login?next=")

            queue = client.get("/registry/review/submissions")
            assert queue.status_code == 401
            assert queue.json()["error"] == "Authentication required."

            submit = client.post(
                "/registry/submit",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network"],
                    "requested_level": "basic",
                },
            )
            assert submit.status_code == 401

            catalog = client.get("/registry", follow_redirects=False)
            assert catalog.status_code == 200
            assert "PureCipher Secured MCP Registry" in catalog.text

            publish_page = client.get("/registry/publish", follow_redirects=False)
            assert publish_page.status_code == 303
            assert publish_page.headers["location"].startswith("/registry/login?next=")

            preflight = client.post(
                "/registry/preflight",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network"],
                    "requested_level": "basic",
                },
            )
            assert preflight.status_code == 200
            assert preflight.json()["effective_certification_level"] == "basic"

    def test_publisher_can_submit_but_cannot_access_review_queue(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            login = client.post(
                "/registry/login",
                json={"username": "publisher", "password": "publisher123"},
            )
            assert login.status_code == 200
            assert login.json()["session"]["role"] == "publisher"

            submit = client.post(
                "/registry/submit",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network"],
                    "requested_level": "basic",
                },
            )
            assert submit.status_code == 201
            assert submit.json()["accepted"] is True

            queue = client.get("/registry/review/submissions")
            assert queue.status_code == 403
            assert queue.json()["error"] == "Reviewer or admin role required."

            publish_page = client.get("/registry/publish")
            assert publish_page.status_code == 200
            assert 'href="/registry/review"' not in publish_page.text

    def test_viewer_cannot_submit(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            login = client.post(
                "/registry/login",
                json={"username": "viewer", "password": "viewer123"},
            )
            assert login.status_code == 200
            assert login.json()["session"]["role"] == "viewer"

            submit = client.post(
                "/registry/submit",
                json={
                    "manifest": _manifest().to_dict(),
                    "display_name": "Weather Lookup",
                    "categories": ["network"],
                    "requested_level": "basic",
                },
            )
            assert submit.status_code == 403
            assert (
                "Publisher, reviewer, or admin role required" in submit.json()["error"]
            )

    def test_policy_routes_require_reviewer_or_admin(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            unauthenticated = client.get("/registry/policy")
            assert unauthenticated.status_code == 401

            viewer_login = client.post(
                "/registry/login",
                json={"username": "viewer", "password": "viewer123"},
            )
            assert viewer_login.status_code == 200

            forbidden = client.get("/registry/policy")
            assert forbidden.status_code == 403

    def test_reviewer_can_manage_policy_chain(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            login = client.post(
                "/registry/login",
                json={"username": "reviewer", "password": "reviewer123"},
            )
            assert login.status_code == 200

            status = client.get("/registry/policy")
            assert status.status_code == 200
            payload = status.json()
            assert payload["policy"]["provider_count"] == 1
            assert payload["versions"]["version_count"] == 1

            add_response = client.post(
                "/registry/policy/proposals",
                json={
                    "action": "add",
                    "config": {"type": "denylist", "denied": ["admin-*"]},
                    "description": "Protect admin tools",
                },
            )
            assert add_response.status_code == 200
            proposal_id = add_response.json()["proposal"]["proposal_id"]
            assert add_response.json()["proposal"]["status"] == "validated"

            proposal_queue = client.get("/registry/policy/proposals")
            assert proposal_queue.status_code == 200
            assert proposal_queue.json()["pending_count"] == 1

            assign_response = client.post(
                f"/registry/policy/proposals/{proposal_id}/assign",
                json={
                    "reviewer": "reviewer",
                    "note": "Taking ownership before simulation.",
                },
            )
            assert assign_response.status_code == 200
            assert assign_response.json()["proposal"]["assigned_reviewer"] == "reviewer"

            simulate_response = client.post(
                f"/registry/policy/proposals/{proposal_id}/simulate",
            )
            assert simulate_response.status_code == 200
            assert simulate_response.json()["proposal"]["status"] == "simulated"

            approve_response = client.post(
                f"/registry/policy/proposals/{proposal_id}/approve",
                json={"note": "Ready for release."},
            )
            assert approve_response.status_code == 200
            assert approve_response.json()["proposal"]["status"] == "approved"

            deploy_response = client.post(
                f"/registry/policy/proposals/{proposal_id}/deploy",
                json={"note": "Applying to live chain."},
            )
            assert deploy_response.status_code == 200
            assert deploy_response.json()["policy"]["provider_count"] == 2
            trail = deploy_response.json()["proposal"]["decision_trail"]
            events = [item["event"] for item in trail]
            assert "assigned" in events
            assert events[-3:] == ["simulated", "approved", "deployed"]

            rollback_response = client.post(
                "/registry/policy/versions/rollback",
                json={
                    "version_number": 1,
                    "reason": "Back to baseline",
                },
            )
            assert rollback_response.status_code == 200
            assert rollback_response.json()["policy"]["provider_count"] == 1

    def test_reviewer_can_approve_but_admin_is_required_to_suspend(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
            require_moderation=True,
        )
        result = registry.submit_tool(
            _manifest(),
            display_name="Weather Lookup",
            categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
            requested_level=CertificationLevel.BASIC,
        )
        assert result.listing is not None

        app = registry.http_app()

        with TestClient(app) as client:
            reviewer_login = client.post(
                "/registry/login",
                json={"username": "reviewer", "password": "reviewer123"},
            )
            assert reviewer_login.status_code == 200

            queue = client.get("/registry/review/submissions")
            assert queue.status_code == 200
            pending_item = queue.json()["sections"]["pending_review"][0]
            assert "approve" in pending_item["available_actions"]
            assert "suspend" not in pending_item["available_actions"]

            approve = client.post(
                f"/registry/review/{result.listing.listing_id}/approve",
                json={"moderator_id": "reviewer-1", "reason": "Approved."},
            )
            assert approve.status_code == 200
            assert approve.json()["listing"]["status"] == "published"

            suspend_denied = client.post(
                f"/registry/review/{result.listing.listing_id}/suspend",
                json={"moderator_id": "reviewer-1", "reason": "No access."},
            )
            assert suspend_denied.status_code == 403
            assert suspend_denied.json()["error"] == "Admin role required."

            admin_login = client.post(
                "/registry/login",
                json={"username": "admin", "password": "admin123"},
            )
            assert admin_login.status_code == 200

            suspend = client.post(
                f"/registry/review/{result.listing.listing_id}/suspend",
                json={"moderator_id": "admin-1", "reason": "Hold for review."},
            )
            assert suspend.status_code == 200
            assert suspend.json()["listing"]["status"] == "suspended"

    def test_login_api_rejects_invalid_credentials(self):
        registry = PureCipherRegistry(
            signing_secret=TEST_SIGNING_SECRET,
            auth_settings=_auth_settings(),
        )
        app = registry.http_app()

        with TestClient(app) as client:
            response = client.post(
                "/registry/login",
                json={"username": "admin", "password": "wrong-password"},
            )

            assert response.status_code == 401
            assert response.json()["error"] == "Invalid username or password."
