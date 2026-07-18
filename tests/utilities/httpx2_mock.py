"""Local ``httpx_mock`` fixture backed by httpx2.

FastMCP no longer depends on the legacy ``httpx`` package, so the third-party
``pytest-httpx`` plugin (which patches ``httpx``) can no longer intercept the
``httpx2`` clients that the auth providers and OpenAPI integration create
internally.

This module provides a drop-in replacement for the small slice of the
``pytest_httpx`` API that the test suite relies on (``add_response``,
``add_exception``, ``get_request`` and ``get_requests``), backed by ``httpx2``.
Interception works exactly like ``pytest-httpx``: the transport request handlers
are monkeypatched at the class level so every client is intercepted regardless
of where it is constructed. Matching and teardown-assertion semantics mirror
``pytest-httpx`` so the ported tests keep their assertions verbatim.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Generator
from typing import Any

import httpx2
import pytest
from pytest import FixtureRequest, MonkeyPatch

__all__ = ["HTTPXMock", "httpx_mock"]


def _params_dict(params: httpx2.QueryParams) -> dict[str, str | list[str]]:
    """Query params as a dict, order-insensitive, mirroring pytest-httpx."""
    result: dict[str, str | list[str]] = {}
    for key in params:
        values = params.get_list(key)
        result[key] = values if len(values) > 1 else values[0]
    return result


def _url_match(expected: re.Pattern[str] | httpx2.URL, received: httpx2.URL) -> bool:
    """Full URL match with query-parameter order insensitivity.

    A ``re.Pattern`` matches against the string form of the received URL,
    mirroring ``pytest-httpx``.
    """
    if isinstance(expected, re.Pattern):
        return expected.match(str(received)) is not None
    if _params_dict(expected.params) != _params_dict(received.params):
        return False
    return expected.copy_with(query=None) == received.copy_with(query=None)


class _Matcher:
    def __init__(
        self,
        url: str | re.Pattern[str] | httpx2.URL | None,
        method: str | None,
    ) -> None:
        self.url = httpx2.URL(url) if isinstance(url, str) else url
        self.method = method.upper() if method else method
        self.nb_calls = 0

    def match(self, request: httpx2.Request) -> bool:
        if self.url is not None and not _url_match(self.url, request.url):
            return False
        if self.method is not None and request.method != self.method:
            return False
        return True

    def __str__(self) -> str:
        description = f"Match {self.method or 'any'} request"
        if self.url is not None:
            description += f" on {self.url}"
        return description


def _unread(response: httpx2.Response) -> httpx2.Response:
    """Allow the response body to be read on the client side."""
    response.is_stream_consumed = False
    response.is_closed = False
    if hasattr(response, "_content"):
        del response._content
    return response


class HTTPXMock:
    """Minimal httpx2-backed reimplementation of ``pytest_httpx.HTTPXMock``."""

    def __init__(self) -> None:
        self._callbacks: list[
            tuple[_Matcher, Callable[[httpx2.Request], httpx2.Response]]
        ] = []
        self._requests: list[httpx2.Request] = []
        self._requests_not_matched: list[httpx2.Request] = []

    def add_response(
        self,
        status_code: int = 200,
        headers: Any = None,
        content: bytes | None = None,
        text: str | None = None,
        html: str | None = None,
        stream: Any = None,
        json: Any = None,
        *,
        url: str | re.Pattern[str] | httpx2.URL | None = None,
        method: str | None = None,
    ) -> None:
        json = copy.deepcopy(json) if json is not None else None

        def callback(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                status_code=status_code,
                headers=headers,
                content=content,
                text=text,
                html=html,
                json=json,
                stream=stream,
            )

        self._callbacks.append((_Matcher(url, method), callback))

    def add_exception(
        self,
        exception: BaseException,
        *,
        url: str | re.Pattern[str] | httpx2.URL | None = None,
        method: str | None = None,
    ) -> None:
        def callback(request: httpx2.Request) -> httpx2.Response:
            if isinstance(exception, httpx2.RequestError):
                exception.request = request
            raise exception

        self._callbacks.append((_Matcher(url, method), callback))

    def _get_callback(
        self, request: httpx2.Request
    ) -> Callable[[httpx2.Request], httpx2.Response] | None:
        matching = [
            (matcher, callback)
            for matcher, callback in self._callbacks
            if matcher.match(request)
        ]
        if not matching:
            return None
        # First not-yet-used callback wins; otherwise reuse the last match.
        for matcher, callback in matching:
            if not matcher.nb_calls:
                matcher.nb_calls += 1
                return callback
        matcher, callback = matching[-1]
        matcher.nb_calls += 1
        return callback

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self._requests.append(request)
        callback = self._get_callback(request)
        if callback is None:
            self._requests_not_matched.append(request)
            raise httpx2.TimeoutException(
                f"No response can be found for {request.method} request on "
                f"{request.url}",
                request=request,
            )
        return _unread(callback(request))

    def _handle_request(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        return self._handle(request)

    async def _handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        return self._handle(request)

    def get_requests(
        self,
        *,
        url: str | re.Pattern[str] | httpx2.URL | None = None,
        method: str | None = None,
    ) -> list[httpx2.Request]:
        matcher = _Matcher(url, method)
        return [request for request in self._requests if matcher.match(request)]

    def get_request(
        self,
        *,
        url: str | re.Pattern[str] | httpx2.URL | None = None,
        method: str | None = None,
    ) -> httpx2.Request | None:
        requests = self.get_requests(url=url, method=method)
        assert len(requests) <= 1, (
            f"More than one request ({len(requests)}) matched, use get_requests "
            "instead or refine your filters."
        )
        return requests[0] if requests else None

    def reset(self) -> None:
        self._callbacks.clear()
        self._requests.clear()
        self._requests_not_matched.clear()

    def _assert_options(self) -> None:
        not_requested = [
            str(matcher) for matcher, _ in self._callbacks if not matcher.nb_calls
        ]
        assert not not_requested, (
            "The following responses are mocked but not requested:\n"
            + "\n".join(f"- {matcher}" for matcher in not_requested)
        )
        not_matched = [
            f"- {request.method} request on {request.url}"
            for request in self._requests_not_matched
        ]
        assert not not_matched, (
            "The following requests were not expected:\n" + "\n".join(not_matched)
        )


@pytest.fixture
def httpx_mock(
    monkeypatch: MonkeyPatch,
    request: FixtureRequest,
) -> Generator[HTTPXMock, None, None]:
    mock = HTTPXMock()

    def mocked_handle_request(
        transport: httpx2.HTTPTransport, request: httpx2.Request
    ) -> httpx2.Response:
        return mock._handle_request(request)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", mocked_handle_request)

    async def mocked_handle_async_request(
        transport: httpx2.AsyncHTTPTransport, request: httpx2.Request
    ) -> httpx2.Response:
        return await mock._handle_async_request(request)

    monkeypatch.setattr(
        httpx2.AsyncHTTPTransport,
        "handle_async_request",
        mocked_handle_async_request,
    )

    yield mock
    try:
        mock._assert_options()
    finally:
        mock.reset()
