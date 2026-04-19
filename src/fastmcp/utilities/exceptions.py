from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx
from exceptiongroup import BaseExceptionGroup

import fastmcp
from fastmcp.exceptions import (
    MCPAuthorizationError,
    MCPConnectionError,
    MCPTimeoutError,
    MCPTransportError,
    NotFoundError,
)


def iter_exc(group: BaseExceptionGroup):
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from iter_exc(exc)
        else:
            yield exc


def _exception_handler(group: BaseExceptionGroup):
    for leaf in iter_exc(group):
        if isinstance(leaf, httpx.HTTPStatusError):
            status_code = leaf.response.status_code
            if status_code == 401:
                raise MCPAuthorizationError(
                    f"Authorization failed: {leaf}",
                    subtype="invalid_token",
                ) from leaf
            elif status_code == 404:
                raise NotFoundError(
                    f"Not found: {leaf}",
                    resource_type="generic",
                ) from leaf
            elif 400 <= status_code < 500:
                raise MCPTransportError(
                    f"HTTP error {status_code}: {leaf}",
                    data={"status_code": status_code},
                    transport="http",
                ) from leaf
            else:
                raise leaf
        elif isinstance(leaf, httpx.ConnectTimeout):
            raise MCPTimeoutError(
                f"Connection timeout: {leaf}",
                transport="http",
            ) from leaf
        elif isinstance(leaf, httpx.TimeoutException):
            raise MCPTimeoutError(
                f"Request timeout: {leaf}",
                transport="http",
            ) from leaf
        elif isinstance(leaf, httpx.ConnectError):
            raise MCPConnectionError(
                f"Connection error: {leaf}",
                transport="http",
            ) from leaf
        raise leaf


_catch_handlers: Mapping[
    type[BaseException] | Iterable[type[BaseException]],
    Callable[[BaseExceptionGroup[Any]], Any],
] = {
    Exception: _exception_handler,
}


def get_catch_handlers() -> Mapping[
    type[BaseException] | Iterable[type[BaseException]],
    Callable[[BaseExceptionGroup[Any]], Any],
]:
    if fastmcp.settings.client_raise_first_exceptiongroup_error:
        return _catch_handlers
    else:
        return {}
