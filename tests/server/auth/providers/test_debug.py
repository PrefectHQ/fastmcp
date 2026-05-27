import pytest

from fastmcp.server.auth.providers.debug import DebugTokenVerifier


class TestDebugTokenVerifier:
    async def test_default_validator_accepts_non_empty_tokens(self):
        verifier = DebugTokenVerifier(client_id="client-1", scopes=["read"])

        token = await verifier.verify_token("token-123")

        assert token is not None
        assert token.token == "token-123"
        assert token.client_id == "client-1"
        assert token.scopes == ["read"]
        assert token.claims == {"token": "token-123"}

    @pytest.mark.parametrize("token", ["", "   "])
    async def test_rejects_empty_tokens(self, token):
        verifier = DebugTokenVerifier()

        assert await verifier.verify_token(token) is None

    async def test_sync_validator_can_reject_tokens(self):
        verifier = DebugTokenVerifier(validate=lambda token: token.startswith("valid-"))

        assert await verifier.verify_token("invalid") is None
        assert await verifier.verify_token("valid-token") is not None

    async def test_async_validator_is_awaited(self):
        async def validate(token: str) -> bool:
            return token == "allowed"

        verifier = DebugTokenVerifier(validate=validate)

        assert await verifier.verify_token("denied") is None
        assert await verifier.verify_token("allowed") is not None
