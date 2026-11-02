"""Tests for KeystoneTokenVerifier against a local fake of the Identity v3 API."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2
import pytest

pytest.importorskip("keystoneauth1")

from pydantic import SecretStr

from fastmcp import Client, FastMCP
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.keystone import KeystoneTokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.tests import run_server_async

SERVICE_TOKEN = "service-token"
PROJECT_ID = "project-1"


def _timestamp(delta: timedelta) -> str:
    moment = datetime.now(timezone.utc) + delta
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def user_token_body(**overrides: Any) -> dict[str, Any]:
    token: dict[str, Any] = {
        "methods": ["password"],
        "audit_ids": ["audit-1"],
        "expires_at": _timestamp(timedelta(hours=1)),
        "issued_at": _timestamp(timedelta(minutes=-1)),
        "user": {
            "id": "user-1",
            "name": "alice",
            "domain": {"id": "default", "name": "Default"},
        },
        "project": {
            "id": PROJECT_ID,
            "name": "demo",
            "domain": {"id": "default", "name": "Default"},
        },
        "roles": [{"id": "r1", "name": "member"}, {"id": "r2", "name": "reader"}],
    }
    token.update(overrides)
    return {"token": {key: value for key, value in token.items() if value is not None}}


class FakeKeystone:
    """The parts of Keystone that keystoneauth1 touches to validate a token."""

    def __init__(self) -> None:
        self.tokens: dict[str, dict[str, Any]] = {}
        self.auth_requests: list[dict[str, Any]] = []
        self.validation_requests: list[dict[str, str]] = []
        self.validation_paths: list[str] = []
        self.validation_status: int | None = None
        self.url = ""


def _handler_for(keystone: FakeKeystone) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(
            self,
            status: int,
            body: dict[str, Any],
            headers: dict[str, str] | None = None,
        ) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            keystone.auth_requests.append(json.loads(self.rfile.read(length)))
            body = user_token_body(
                catalog=[
                    {
                        "type": "identity",
                        "name": "keystone",
                        "id": "identity",
                        "endpoints": [
                            {
                                "id": "public",
                                "interface": "public",
                                "region": "RegionOne",
                                "region_id": "RegionOne",
                                "url": f"{keystone.url}/v3",
                            }
                        ],
                    }
                ]
            )
            self._send(201, body, {"X-Subject-Token": SERVICE_TOKEN})

        def do_GET(self) -> None:
            if self.path.split("?")[0].rstrip("/") == "/v3":
                self._send(
                    200,
                    {
                        "version": {
                            "id": "v3.14",
                            "status": "stable",
                            "links": [
                                {"rel": "self", "href": f"{keystone.url}/v3/"},
                            ],
                        }
                    },
                )
                return

            keystone.validation_requests.append(dict(self.headers))
            keystone.validation_paths.append(self.path)
            if keystone.validation_status is not None:
                self._send(keystone.validation_status, {"error": {}})
            elif self.headers.get("X-Auth-Token") != SERVICE_TOKEN:
                self._send(401, {"error": {}})
            elif (token := self.headers["X-Subject-Token"]) in keystone.tokens:
                self._send(200, keystone.tokens[token])
            else:
                self._send(404, {"error": {}})

    return Handler


@pytest.fixture
def keystone() -> Iterator[FakeKeystone]:
    fake = FakeKeystone()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(fake))
    fake.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield fake
    server.shutdown()
    server.server_close()
    thread.join()


def make_verifier(keystone: FakeKeystone, **kwargs: Any) -> KeystoneTokenVerifier:
    return KeystoneTokenVerifier(
        auth_url=f"{keystone.url}/v3",
        username="mcp-service",
        password="secret",
        project_name="service",
        **kwargs,
    )


class TestConfiguration:
    def test_requires_credentials(self):
        with pytest.raises(ValueError, match="either username and password"):
            KeystoneTokenVerifier(auth_url="http://keystone/v3")

    def test_rejects_both_credential_types(self):
        with pytest.raises(ValueError, match="either username and password"):
            KeystoneTokenVerifier(
                auth_url="http://keystone/v3",
                username="user",
                password="secret",
                application_credential_id="id",
                application_credential_secret="secret",
            )

    def test_rejects_username_without_password(self):
        with pytest.raises(ValueError, match="provided together"):
            KeystoneTokenVerifier(auth_url="http://keystone/v3", username="user")

    def test_rejects_application_credential_without_secret(self):
        with pytest.raises(ValueError, match="provided together"):
            KeystoneTokenVerifier(
                auth_url="http://keystone/v3",
                application_credential_id="id",
            )


class TestVerifyToken:
    async def test_valid_token(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()

        result = await make_verifier(keystone).verify_token("user-token")

        assert result is not None
        assert result.token == "user-token"
        assert result.client_id == "user-1"
        assert result.subject == "user-1"
        assert result.scopes == ["member", "reader"]
        assert result.expires_at is not None
        assert result.claims["project_id"] == PROJECT_ID
        assert result.claims["project_name"] == "demo"
        assert result.claims["username"] == "alice"
        assert result.claims["roles"] == ["member", "reader"]

    async def test_validation_call_shape(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()

        await make_verifier(keystone).verify_token("user-token")

        assert keystone.validation_paths == ["/v3/auth/tokens?nocatalog"]
        headers = keystone.validation_requests[0]
        assert headers["X-Auth-Token"] == SERVICE_TOKEN
        assert headers["X-Subject-Token"] == "user-token"

    @pytest.mark.parametrize("suffix", ["", "/", "/v3/"])
    async def test_auth_url_forms(self, keystone: FakeKeystone, suffix: str):
        keystone.tokens["user-token"] = user_token_body()
        verifier = KeystoneTokenVerifier(
            auth_url=f"{keystone.url}{suffix}",
            username="mcp-service",
            password="secret",
        )

        assert await verifier.verify_token("user-token") is not None

    async def test_service_user_auth_request(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()
        verifier = KeystoneTokenVerifier(
            auth_url=keystone.url,
            username="mcp-service",
            password=SecretStr("secret"),
            user_domain_name="services",
            project_name="service",
            project_domain_name="ops",
        )

        await verifier.verify_token("user-token")

        auth = keystone.auth_requests[0]["auth"]
        assert auth["identity"]["password"]["user"] == {
            "name": "mcp-service",
            "password": "secret",
            "domain": {"name": "services"},
        }
        assert auth["scope"]["project"] == {
            "name": "service",
            "domain": {"name": "ops"},
        }

    async def test_application_credential_auth_request(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()
        verifier = KeystoneTokenVerifier(
            auth_url=keystone.url,
            application_credential_id="cred-id",
            application_credential_secret=SecretStr("cred-secret"),
        )

        assert await verifier.verify_token("user-token") is not None

        identity = keystone.auth_requests[0]["auth"]["identity"]
        assert identity["methods"] == ["application_credential"]
        assert identity["application_credential"] == {
            "id": "cred-id",
            "secret": "cred-secret",
        }

    async def test_service_token_reused_between_validations(
        self, keystone: FakeKeystone
    ):
        keystone.tokens["user-token"] = user_token_body()
        verifier = make_verifier(keystone)

        await verifier.verify_token("user-token")
        await verifier.verify_token("user-token")

        assert len(keystone.auth_requests) == 1
        assert len(keystone.validation_requests) == 2


class TestRejection:
    async def test_unknown_token(self, keystone: FakeKeystone):
        assert await make_verifier(keystone).verify_token("unknown") is None

    @pytest.mark.parametrize("token", ["", "   "])
    async def test_empty_token_never_reaches_keystone(
        self, keystone: FakeKeystone, token: str
    ):
        assert await make_verifier(keystone).verify_token(token) is None
        assert keystone.auth_requests == []

    async def test_expired_token(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body(
            expires_at=_timestamp(timedelta(minutes=-1))
        )

        assert await make_verifier(keystone).verify_token("user-token") is None

    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    async def test_keystone_refusal_fails_closed(
        self, keystone: FakeKeystone, status: int
    ):
        keystone.tokens["user-token"] = user_token_body()
        keystone.validation_status = status

        assert await make_verifier(keystone).verify_token("user-token") is None

    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_service_side_failures_are_logged_as_warnings(
        self,
        keystone: FakeKeystone,
        status: int,
        caplog: pytest.LogCaptureFixture,
    ):
        keystone.validation_status = status

        with caplog.at_level(logging.WARNING, logger="fastmcp"):
            await make_verifier(keystone).verify_token("user-token")

        assert f"HTTP {status}" in caplog.text

    async def test_unknown_token_is_not_logged_as_a_warning(
        self, keystone: FakeKeystone, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.WARNING, logger="fastmcp"):
            await make_verifier(keystone).verify_token("unknown")

        assert "Keystone" not in caplog.text

    async def test_unreachable_keystone_fails_closed(self):
        verifier = KeystoneTokenVerifier(
            auth_url="http://127.0.0.1:1/v3",
            username="mcp-service",
            password="secret",
            timeout_seconds=1,
        )

        assert await verifier.verify_token("user-token") is None

    async def test_malformed_response_fails_closed(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = {"token": {"user": {"id": "user-1"}}}

        assert await make_verifier(keystone).verify_token("user-token") is None

    @pytest.mark.parametrize(
        "rules",
        [[], [{"service": "compute", "method": "GET", "path": "/v2.1/servers"}]],
    )
    async def test_access_rule_restricted_token(
        self, keystone: FakeKeystone, rules: list[dict[str, str]]
    ):
        keystone.tokens["user-token"] = user_token_body(
            application_credential={
                "id": "cred-1",
                "name": "restricted",
                "restricted": True,
                "access_rules": rules,
            }
        )

        assert await make_verifier(keystone).verify_token("user-token") is None

    async def test_unrestricted_application_credential_token(
        self, keystone: FakeKeystone
    ):
        keystone.tokens["user-token"] = user_token_body(
            application_credential={
                "id": "cred-1",
                "name": "plain",
                "restricted": True,
            }
        )

        result = await make_verifier(keystone).verify_token("user-token")

        assert result is not None
        assert result.claims["application_credential_id"] == "cred-1"


class TestRestrictions:
    async def test_required_scopes_match_roles(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()

        allowed = make_verifier(keystone, required_scopes=["member"])
        denied = make_verifier(keystone, required_scopes=["admin"])

        assert await allowed.verify_token("user-token") is not None
        assert await denied.verify_token("user-token") is None

    async def test_allowed_project(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()

        allowed = make_verifier(keystone, allowed_project_ids=[PROJECT_ID])
        denied = make_verifier(keystone, allowed_project_ids=["another-project"])

        assert await allowed.verify_token("user-token") is not None
        assert await denied.verify_token("user-token") is None

    async def test_project_allowlist_rejects_unscoped_token(
        self, keystone: FakeKeystone
    ):
        keystone.tokens["user-token"] = user_token_body(project=None, roles=None)

        open_verifier = make_verifier(keystone)
        strict_verifier = make_verifier(keystone, allowed_project_ids=[PROJECT_ID])

        unscoped = await open_verifier.verify_token("user-token")
        assert unscoped is not None
        assert unscoped.scopes == []
        assert await strict_verifier.verify_token("user-token") is None


class TestCaching:
    async def test_disabled_by_default_so_revocation_is_immediate(
        self, keystone: FakeKeystone
    ):
        keystone.tokens["user-token"] = user_token_body()
        verifier = make_verifier(keystone)

        assert await verifier.verify_token("user-token") is not None

        del keystone.tokens["user-token"]

        assert await verifier.verify_token("user-token") is None

    async def test_enabled_cache_skips_keystone(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()
        verifier = make_verifier(keystone, cache_ttl_seconds=60)

        first = await verifier.verify_token("user-token")
        second = await verifier.verify_token("user-token")

        assert first is not None and first == second
        assert len(keystone.validation_requests) == 1

    async def test_rejections_are_not_cached(self, keystone: FakeKeystone):
        verifier = make_verifier(keystone, cache_ttl_seconds=60)

        assert await verifier.verify_token("user-token") is None

        keystone.tokens["user-token"] = user_token_body()

        assert await verifier.verify_token("user-token") is not None


class TestEndToEnd:
    async def test_bearer_token_over_http(self, keystone: FakeKeystone):
        keystone.tokens["user-token"] = user_token_body()
        mcp = FastMCP("keystone", auth=make_verifier(keystone))

        @mcp.tool
        def whoami() -> dict[str, Any]:
            token = get_access_token()
            assert token is not None
            return {"user": token.client_id, "project": token.claims["project_id"]}

        async with run_server_async(mcp, transport="http") as url:
            transport = StreamableHttpTransport(url, auth=BearerAuth("user-token"))
            async with Client(transport) as client:
                result = await client.call_tool("whoami")
            assert result.data == {"user": "user-1", "project": PROJECT_ID}

            async with httpx2.AsyncClient() as http:
                response = await http.post(
                    url,
                    headers={"Authorization": "Bearer bad"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                )
            assert response.status_code == 401
