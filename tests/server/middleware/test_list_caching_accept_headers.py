"""Regression tests for issue #4461: list responses cached across content-type changes."""

from fastmcp.server.middleware.caching import (
    ANONYMOUS_AUTH_KEY,
    _make_list_cache_key,
)


class TestListCacheKeyAcceptHeaderPartitioning:
    """Regression tests for issue #4461: list caches must vary by Accept header."""

    def test_list_cache_key_differs_by_accept_header(self, monkeypatch):
        accepts = iter(["text/plain", "application/json"])
        monkeypatch.setattr(
            "fastmcp.server.dependencies.get_http_headers",
            lambda **kwargs: {"accept": next(accepts)},
        )
        plain_key = _make_list_cache_key()
        json_key = _make_list_cache_key()

        assert plain_key != json_key

    def test_list_cache_key_without_accept_uses_auth_partition(self, monkeypatch):
        monkeypatch.setattr(
            "fastmcp.server.dependencies.get_http_headers",
            lambda **kwargs: {},
        )
        assert _make_list_cache_key() == ANONYMOUS_AUTH_KEY

    def test_list_cache_key_normalizes_equivalent_headers(self, monkeypatch):
        monkeypatch.setattr(
            "fastmcp.server.dependencies.get_http_headers",
            lambda **kwargs: {"accept": "application/json, text/plain"},
        )
        key1 = _make_list_cache_key()

        monkeypatch.setattr(
            "fastmcp.server.dependencies.get_http_headers",
            lambda **kwargs: {"accept": "text/plain, application/json"},
        )
        key2 = _make_list_cache_key()

        assert key1 == key2
