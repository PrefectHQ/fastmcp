"""Wire-behavior tests for the httpx2-based upstream OAuth2 client.

This client replaced authlib's `AsyncOAuth2Client` for upstream token-endpoint
calls; these tests pin the wire format authlib produced so the migration is
observable: form-encoded bodies, client authentication methods, falsy-param
dropping, refresh-token injection, expires_at derivation, and error mapping.
"""

import base64
import time
from urllib.parse import parse_qs

import httpx2
import pytest
from authlib.integrations.base_client import OAuthError

from fastmcp.server.auth.oauth_proxy.upstream import AsyncOAuth2Client
from tests.utilities.httpx2_mock import HTTPXMock

TOKEN_URL = "https://idp.example.com/token"


def _form(request: httpx2.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"))


class TestClientAuthMethods:
    async def test_default_is_client_secret_basic(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        await client.fetch_token(TOKEN_URL, code="abc", redirect_uri="https://cb")
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        expected = base64.b64encode(b"cid:sec").decode("ascii")
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert "client_secret" not in _form(request)

    async def test_client_secret_post(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
        client = AsyncOAuth2Client(
            client_id="cid",
            client_secret="sec",
            token_endpoint_auth_method="client_secret_post",
        )
        await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        form = _form(request)
        assert form["client_id"] == ["cid"]
        assert form["client_secret"] == ["sec"]
        assert "Authorization" not in request.headers

    async def test_none_auth_sends_client_id_only(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
        client = AsyncOAuth2Client(client_id="cid", token_endpoint_auth_method="none")
        await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        form = _form(request)
        assert form["client_id"] == ["cid"]
        assert "client_secret" not in form
        assert "Authorization" not in request.headers

    async def test_unsupported_method_raises(self):
        client = AsyncOAuth2Client(
            client_id="cid", token_endpoint_auth_method="private_key_jwt"
        )
        with pytest.raises(ValueError, match="Unsupported token_endpoint_auth_method"):
            await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()


class TestFetchToken:
    async def test_authorization_code_body(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        await client.fetch_token(
            TOKEN_URL,
            code="abc",
            redirect_uri="https://cb",
            code_verifier="ver",
            scope="openid email",
        )
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert (
            request.headers["Content-Type"]
            == "application/x-www-form-urlencoded;charset=UTF-8"
        )
        form = _form(request)
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["abc"]
        assert form["redirect_uri"] == ["https://cb"]
        assert form["code_verifier"] == ["ver"]
        assert form["scope"] == ["openid email"]

    async def test_falsy_params_dropped(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "tok"})
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        await client.fetch_token(TOKEN_URL, code="abc", scope=None, audience="")
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        form = _form(request)
        assert "scope" not in form
        assert "audience" not in form

    async def test_expires_at_derived_from_expires_in(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=TOKEN_URL, json={"access_token": "tok", "expires_in": 3600}
        )
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        before = int(time.time())
        token = await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()

        assert before + 3600 <= token["expires_at"] <= int(time.time()) + 3600

    async def test_oauth_error_response_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=TOKEN_URL,
            status_code=400,
            json={"error": "invalid_grant", "error_description": "bad code"},
        )
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        with pytest.raises(OAuthError, match="invalid_grant"):
            await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()

    async def test_server_error_raises_http_status_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, status_code=503, text="down")
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        with pytest.raises(httpx2.HTTPStatusError):
            await client.fetch_token(TOKEN_URL, code="abc")
        await client.aclose()


class TestRefreshToken:
    async def test_refresh_body(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=TOKEN_URL, json={"access_token": "new", "refresh_token": "rot"}
        )
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        token = await client.refresh_token(
            TOKEN_URL, refresh_token="old", scope="openid"
        )
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        form = _form(request)
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["old"]
        assert form["scope"] == ["openid"]
        assert token["refresh_token"] == "rot"

    async def test_unrotated_refresh_token_injected(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "new"})
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        token = await client.refresh_token(TOKEN_URL, refresh_token="old")
        await client.aclose()

        assert token["refresh_token"] == "old"

    async def test_none_scope_omitted(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TOKEN_URL, json={"access_token": "new"})
        client = AsyncOAuth2Client(client_id="cid", client_secret="sec")
        await client.refresh_token(TOKEN_URL, refresh_token="old", scope=None)
        await client.aclose()

        request = httpx_mock.get_request()
        assert request is not None
        assert "scope" not in _form(request)
