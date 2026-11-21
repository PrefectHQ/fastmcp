"""A stateful model of the OAuth proxy's token lifecycle.

Rules issue authorization codes for two registered clients, redeem them, use
and rotate refresh tokens, revoke tokens, and check access tokens, sometimes
two requests at once. Each redemption goes through the same steps the token
endpoint takes (`load_*` for the requesting client, then `exchange_*`). The
upstream identity provider is a fake that issues opaque tokens, rotates
refresh tokens or not (drawn), and honours revocation.

The model is RFC 6749 and RFC 7009 as the proxy documents them:

- an authorization code is redeemed at most once, and only by the client it
  was issued to (RFC 6749 4.1.2, 10.5)
- a refresh token works only for its own client; after a refresh, the
  presented refresh token no longer works and the returned one does
  (refresh-token rotation, RFC 9700 4.14.2)
- of two concurrent redemptions of one code or one refresh token, exactly one
  succeeds
- a revoked refresh token never works again; a revoked access token stops
  validating and never validates again (RFC 7009 2.1)
- revoking an access token may revoke its whole grant (RFC 7009 2.1): every
  access token of the grant stops validating until a refresh gives the grant
  a new upstream token
- an access token that was issued and not revoked validates while its grant
  has a live upstream token
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx2
from hypothesis import event, note
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    precondition,
    rule,
)
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AccessToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

import fastmcp.server.auth.oauth_proxy.proxy as proxy_module
from fastmcp.server.auth.auth import RefreshToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from fastmcp.server.auth.oauth_proxy.revocation import RevocationProvider

from .known import still_open

CLIENTS = ["alice", "bob"]
REDIRECT = "http://localhost:12345/callback"
REVOKE_URL = "https://idp.example.com/revoke"


class FakeIdP:
    """Issues opaque upstream tokens and answers validation and revocation."""

    def __init__(self, rotates: bool) -> None:
        self.rotates = rotates
        self.live: set[str] = set()
        self.count = 0

    def issue(self, kind: str) -> str:
        self.count += 1
        token = f"{kind}-{self.count}"
        self.live.add(token)
        return token

    def token_set(self) -> dict:
        return {
            "access_token": self.issue("at"),
            "refresh_token": self.issue("rt"),
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    async def refresh(self, refresh_token: str | None = None, **_) -> dict:
        if refresh_token not in self.live:
            raise RuntimeError("upstream refresh token is not live")
        tokens = self.token_set()
        if not self.rotates:
            self.live.discard(tokens["refresh_token"])
            tokens["refresh_token"] = refresh_token
        else:
            self.live.discard(refresh_token)
        return tokens

    async def verify(self, token: str) -> AccessToken | None:
        if token not in self.live:
            return None
        return AccessToken(
            token=token,
            client_id="upstream",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
        )

    def handle_revoke(self, request: httpx2.Request) -> httpx2.Response:
        form = dict(
            pair.split("=", 1) for pair in request.content.decode().split("&") if pair
        )
        self.live.discard(form.get("token", ""))
        return httpx2.Response(200)


def _yielding(read):
    async def read_then_yield(*args, **kwargs):
        value = await read(*args, **kwargs)
        await asyncio.sleep(0)
        return value

    return read_then_yield


def _revocation_is_per_grant() -> bool:
    """True while revoking an access token and then refreshing revives it.

    The probe runs the minimal history from FINDINGS.md: redeem a code,
    revoke the access token, refresh, and check the revoked token.
    """
    machine = OAuthMachine(probe=True)
    try:
        machine.start(rotates=False)
        machine.issue_code(client="alice")
        tokens = machine.run(machine._redeem_code("alice", "code-0"))
        assert tokens.refresh_token is not None
        assert machine.run(machine._revoke_access(tokens.access_token))
        machine.run(machine._refresh("alice", tokens.refresh_token))
        revived = machine.run(machine.proxy.load_access_token(tokens.access_token))
        return revived is not None
    finally:
        machine.teardown()


class OAuthMachine(RuleBasedStateMachine):
    def __init__(self, probe: bool = False) -> None:
        super().__init__()
        self.probe = probe
        self.loop = asyncio.new_event_loop()
        self.logger = logging.getLogger("fastmcp")
        self.log_level = self.logger.level
        self.logger.setLevel(logging.CRITICAL)
        self.patches: list = []
        # model
        self.codes: dict[str, str] = {}  # live code -> client
        self.used_codes: dict[str, str] = {}  # redeemed or never valid -> client
        self.refresh: dict[str, str] = {}  # live refresh token -> client
        self.dead_refresh: dict[str, str] = {}  # rotated or revoked -> client
        self.access: dict[str, str] = {}  # live access token -> client
        self.revoked_access: set[str] = set()
        # token -> the code it descends from; grants with a revoked access token
        self.grant: dict[str, str] = {}
        self.grants_with_revocation: set[str] = set()
        # grant -> whether its current upstream access token is live
        self.grant_live: dict[str, bool] = {}

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def teardown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.loop.close()
        self.logger.setLevel(self.log_level)

    @initialize(rotates=st.booleans())
    def start(self, rotates: bool) -> None:
        if not self.probe:
            event(f"upstream rotates refresh tokens: {rotates}")
        self.idp = FakeIdP(rotates)
        verifier = Mock()
        verifier.required_scopes = []
        verifier.verify_token = self.idp.verify
        self.proxy = OAuthProxy(
            upstream_authorization_endpoint="https://idp.example.com/authorize",
            upstream_token_endpoint="https://idp.example.com/token",
            upstream_revocation_endpoint=REVOKE_URL,
            upstream_client_id="proxy",
            upstream_client_secret="secret",
            token_verifier=verifier,
            base_url="https://proxy.example.com",
            jwt_signing_key="test-signing-key",
            client_storage=MemoryStore(),
        )
        self.proxy.set_mcp_path("/mcp")
        upstream_client = AsyncMock()
        upstream_client.client_secret = "secret"
        upstream_client.refresh_token = AsyncMock(side_effect=self.idp.refresh)
        real_client = httpx2.AsyncClient
        self.patches = [
            patch.object(
                self.proxy,
                "_create_upstream_oauth_client",
                return_value=upstream_client,
            ),
            patch.object(
                proxy_module.httpx2,
                "AsyncClient",
                lambda **kw: real_client(
                    transport=httpx2.MockTransport(self.idp.handle_revoke)
                ),
            ),
        ]
        # Yield after every store read, so two concurrent redemptions both read
        # before either writes: the interleaving a networked store produces.
        for store in (self.proxy._code_store, self.proxy._refresh_token_store):
            self.patches.append(
                patch.object(store, "get", side_effect=_yielding(store.get))
            )
        for p in self.patches:
            p.start()
        self.clients = {}
        for name in CLIENTS:
            info = OAuthClientInformationFull(
                client_id=name,
                client_secret="s",
                redirect_uris=[AnyUrl(REDIRECT)],
            )
            self.run(self.proxy.register_client(info))
            self.clients[name] = info

    # ---- steps the token endpoint takes ----------------------------------

    async def _redeem_code(self, client: str, code: str):
        info = self.clients[client]
        loaded = await self.proxy.load_authorization_code(info, code)
        if loaded is None:
            raise TokenError("invalid_grant", "unknown code")
        return await self.proxy.exchange_authorization_code(info, loaded)

    async def _refresh(self, client: str, token: str):
        info = self.clients[client]
        loaded = await self.proxy.load_refresh_token(info, token)
        if loaded is None:
            raise TokenError("invalid_grant", "unknown refresh token")
        return await self.proxy.exchange_refresh_token(info, loaded, loaded.scopes)

    async def _revoke_access(self, token: str) -> bool:
        """Revoke as the revocation route does; False when it cannot load."""
        route = RevocationProvider(self.proxy)
        loaded = await route.load_access_token(token)
        if loaded is None:
            return False
        await route.revoke_token(loaded)
        return True

    def _issued(self, client: str, tokens, grant: str) -> None:
        self.access[tokens.access_token] = client
        self.grant[tokens.access_token] = grant
        self.grant_live[grant] = True
        if tokens.refresh_token:
            self.refresh[tokens.refresh_token] = client
            self.grant[tokens.refresh_token] = grant

    # ---- rules -------------------------------------------------------------

    @rule(client=st.sampled_from(CLIENTS))
    def issue_code(self, client):
        """The IdP callback stored a code for `client`."""
        code = f"code-{len(self.codes) + len(self.used_codes)}"
        now = time.time()
        self.run(
            self.proxy._code_store.put(
                key=code,
                value=ClientCode(
                    code=code,
                    client_id=client,
                    redirect_uri=REDIRECT,
                    code_challenge="c",
                    code_challenge_method="S256",
                    scopes=["read"],
                    idp_tokens=self.idp.token_set(),
                    expires_at=now + 300,
                    created_at=now,
                ),
            )
        )
        self.codes[code] = client

    def _pick(self, data, live: dict[str, str], dead: dict[str, str]) -> str | None:
        # Insertion order, not sorted: issued tokens are random strings, and
        # a draw must pick the same entry when a history is replayed.
        pool = [*live, *dead]
        return data.draw(st.sampled_from(pool)) if pool else None

    @rule(data=st.data(), client=st.sampled_from(CLIENTS), twice=st.booleans())
    def redeem_code(self, data, client, twice):
        code = self._pick(data, self.codes, self.used_codes)
        if code is None:
            return
        owner = self.codes.get(code)
        should_win = owner == client
        attempts = [self._redeem_code(client, code) for _ in range(2 if twice else 1)]

        async def go():
            return await asyncio.gather(*attempts, return_exceptions=True)

        results = self.run(go())
        wins = [r for r in results if not isinstance(r, BaseException)]
        for r in results:
            if isinstance(r, BaseException):
                assert isinstance(r, TokenError), f"redeeming raised {r!r}"
        note(f"redeem {code} by {client} (owner {owner}) x{len(attempts)}: {results}")
        event(
            f"redeem code: {'own' if should_win else 'foreign or used'}, x{len(attempts)}"
        )
        assert len(wins) == (1 if should_win else 0), (
            "a code was redeemed a different number of times than the model allows"
        )
        if wins:
            del self.codes[code]
            self.used_codes[code] = client
            self._issued(client, wins[0], grant=code)

    @rule(data=st.data(), client=st.sampled_from(CLIENTS), twice=st.booleans())
    def use_refresh(self, data, client, twice):
        token = self._pick(data, self.refresh, self.dead_refresh)
        if token is None:
            return
        owner = self.refresh.get(token)
        should_win = owner == client
        attempts = [self._refresh(client, token) for _ in range(2 if twice else 1)]

        async def go():
            return await asyncio.gather(*attempts, return_exceptions=True)

        results = self.run(go())
        for r in results:
            if isinstance(r, BaseException):
                assert isinstance(r, TokenError), f"refreshing raised {r!r}"
        wins = [r for r in results if not isinstance(r, BaseException)]
        event(
            f"refresh: {'own' if should_win else 'foreign or dead'}, x{len(attempts)}"
        )
        assert len(wins) == (1 if should_win else 0), (
            "a refresh token worked a different number of times than the model allows"
        )
        if wins:
            del self.refresh[token]
            self.dead_refresh[token] = client
            assert wins[0].refresh_token != token, (
                "refresh returned the presented token"
            )
            self._issued(client, wins[0], grant=self.grant[token])

    @precondition(lambda self: self.refresh or self.dead_refresh)
    @rule(data=st.data())
    def revoke_refresh(self, data):
        token = self._pick(data, self.refresh, self.dead_refresh)
        assert token is not None
        client = self.refresh.get(token) or self.dead_refresh[token]
        rt = RefreshToken(token=token, client_id=client, scopes=["read"])
        self.run(self.proxy.revoke_token(rt))
        event("revoke refresh token")
        if token in self.refresh:
            del self.refresh[token]
            self.dead_refresh[token] = client

    @precondition(lambda self: self.access)
    @rule(data=st.data())
    def revoke_access(self, data):
        token = data.draw(st.sampled_from(list(self.access)))
        # The route loads any genuine FastMCP token, even one whose grant's
        # upstream token is already revoked, so every revocation lands.
        assert self.run(self._revoke_access(token)), "the route refused a token"
        grant = self.grant[token]
        event("revoke access token")
        # Only a token that still validates reaches the upstream token; one
        # already revoked, or whose grant is revoked, is only retired.
        if token not in self.revoked_access and self.grant_live[grant]:
            self.grant_live[grant] = False
        self.revoked_access.add(token)
        self.grants_with_revocation.add(grant)

    @precondition(lambda self: self.access)
    @rule(data=st.data())
    def check_access(self, data):
        token = data.draw(st.sampled_from(list(self.access)))
        got = self.run(self.proxy.load_access_token(token))
        if (
            ACCESS_REVOCATION_PER_GRANT
            and self.grant[token] in self.grants_with_revocation
        ):
            # Known finding: every access token of a grant validates against
            # the grant's latest upstream token, so revoking one revokes the
            # grant's current upstream token and a refresh revives them all.
            event("known: access revocation is per grant and undone by refresh")
            return
        if token in self.revoked_access:
            event("check access: revoked")
            assert got is None, "a revoked access token still validates"
        elif not self.grant_live[self.grant[token]]:
            event("check access: grant revoked")
            assert got is None, "an access token outlived its grant's revocation"
        else:
            event("check access: live")
            assert got is not None, "an issued access token does not validate"


ACCESS_REVOCATION_PER_GRANT = still_open("F10", _revocation_is_per_grant)
TestOAuthProxy = OAuthMachine.TestCase
