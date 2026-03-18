"""Tests for the shared TokenCache utility."""

import time

import pytest

from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.token_cache import TokenCache


def _make_token(
    *,
    token: str = "tok",
    client_id: str = "client-1",
    scopes: list[str] | None = None,
    expires_at: int | None = None,
) -> AccessToken:
    return AccessToken(
        token=token,
        client_id=client_id,
        scopes=scopes or [],
        expires_at=expires_at,
    )


class TestTokenCacheDisabled:
    """Verify behaviour when caching is turned off."""

    @pytest.mark.parametrize(
        "ttl, max_size",
        [
            (None, None),
            (0, 100),
            (300, 0),
        ],
    )
    def test_disabled_configurations(self, ttl: int | None, max_size: int | None):
        cache = TokenCache(ttl_seconds=ttl, max_size=max_size)
        assert not cache.enabled

    def test_negative_ttl_raises(self):
        with pytest.raises(ValueError, match="cache_ttl_seconds must be non-negative"):
            TokenCache(ttl_seconds=-1)

    def test_negative_max_size_raises(self):
        with pytest.raises(ValueError, match="max_cache_size must be non-negative"):
            TokenCache(max_size=-1)

    def test_get_returns_miss_when_disabled(self):
        cache = TokenCache(ttl_seconds=0)
        cache.set("tok", _make_token())
        hit, result = cache.get("tok")
        assert not hit
        assert result is None

    def test_set_is_noop_when_disabled(self):
        cache = TokenCache(ttl_seconds=0)
        cache.set("tok", _make_token())
        assert len(cache._entries) == 0


class TestTokenCacheEnabled:
    """Core get/set behaviour with caching on."""

    @pytest.fixture
    def cache(self) -> TokenCache:
        return TokenCache(ttl_seconds=300, max_size=100)

    def test_enabled(self, cache: TokenCache):
        assert cache.enabled

    def test_set_and_get(self, cache: TokenCache):
        access = _make_token(client_id="user-1")
        cache.set("tok-1", access)

        hit, result = cache.get("tok-1")
        assert hit
        assert result is not None
        assert result.client_id == "user-1"

    def test_miss_for_unknown_token(self, cache: TokenCache):
        hit, result = cache.get("unknown")
        assert not hit
        assert result is None

    def test_different_tokens_cached_separately(self, cache: TokenCache):
        cache.set("tok-a", _make_token(client_id="a"))
        cache.set("tok-b", _make_token(client_id="b"))

        _, a = cache.get("tok-a")
        _, b = cache.get("tok-b")
        assert a is not None and a.client_id == "a"
        assert b is not None and b.client_id == "b"


class TestTokenCacheDefensiveCopy:
    """Mutating a returned token must not affect the cached value."""

    def test_get_returns_deep_copy(self):
        cache = TokenCache(ttl_seconds=300, max_size=100)
        access = _make_token(client_id="orig")
        access.claims = {"key": "original"}
        cache.set("tok", access)

        _, first = cache.get("tok")
        assert first is not None
        first.claims["key"] = "mutated"
        first.scopes.append("admin")

        _, second = cache.get("tok")
        assert second is not None
        assert second.claims["key"] == "original"
        assert "admin" not in second.scopes

    def test_mutating_source_does_not_affect_cache(self):
        cache = TokenCache(ttl_seconds=300, max_size=100)
        access = _make_token(client_id="orig")
        access.claims = {"key": "original"}
        cache.set("tok", access)

        access.claims["key"] = "mutated"

        _, cached = cache.get("tok")
        assert cached is not None
        assert cached.claims["key"] == "original"


class TestTokenCacheTTL:
    """Expiration and TTL behaviour."""

    def test_expired_entry_is_evicted_on_get(self):
        cache = TokenCache(ttl_seconds=300, max_size=100)
        cache.set("tok", _make_token())

        key = cache._hash_token("tok")
        cache._entries[key].expires_at = time.time() - 1

        hit, result = cache.get("tok")
        assert not hit
        assert result is None
        assert key not in cache._entries

    def test_token_expires_at_caps_ttl(self):
        cache = TokenCache(ttl_seconds=300, max_size=100)
        short_exp = int(time.time()) + 30
        cache.set("tok", _make_token(expires_at=short_exp))

        key = cache._hash_token("tok")
        assert cache._entries[key].expires_at <= short_exp

    def test_ttl_used_when_no_token_expiry(self):
        cache = TokenCache(ttl_seconds=60, max_size=100)
        before = time.time()
        cache.set("tok", _make_token(expires_at=None))
        after = time.time()

        key = cache._hash_token("tok")
        entry = cache._entries[key]
        assert before + 60 <= entry.expires_at <= after + 60


class TestTokenCacheSizeLimit:
    """Eviction and size-limit behaviour."""

    def test_evicts_oldest_when_full(self):
        cache = TokenCache(ttl_seconds=300, max_size=2)
        cache.set("tok-0", _make_token(client_id="0"))
        cache.set("tok-1", _make_token(client_id="1"))
        cache.set("tok-2", _make_token(client_id="2"))

        assert len(cache._entries) == 2
        hit_0, _ = cache.get("tok-0")
        assert not hit_0

        hit_1, _ = cache.get("tok-1")
        hit_2, _ = cache.get("tok-2")
        assert hit_1
        assert hit_2

    def test_cleanup_expired_before_eviction(self):
        cache = TokenCache(ttl_seconds=300, max_size=2)
        cache.set("tok-0", _make_token(client_id="0"))
        cache.set("tok-1", _make_token(client_id="1"))

        key_0 = cache._hash_token("tok-0")
        cache._entries[key_0].expires_at = time.time() - 1

        cache.set("tok-2", _make_token(client_id="2"))

        assert len(cache._entries) == 2
        hit_1, _ = cache.get("tok-1")
        hit_2, _ = cache.get("tok-2")
        assert hit_1
        assert hit_2


class TestTokenCacheHashing:
    """SHA-256 key hashing."""

    def test_consistent_hashing(self):
        assert TokenCache._hash_token("abc") == TokenCache._hash_token("abc")

    def test_different_tokens_different_hashes(self):
        assert TokenCache._hash_token("abc") != TokenCache._hash_token("xyz")

    def test_hash_is_64_hex_chars(self):
        h = TokenCache._hash_token("anything")
        assert len(h) == 64
        int(h, 16)  # must be valid hex
