from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx2
from exceptiongroup import BaseExceptionGroup
from mcp import MCPError

import fastmcp


def _is_legacy_httpx_exception(exc: BaseException, exception_type: str) -> bool:
    """Check a legacy-httpx exception without importing the legacy package."""
    return any(
        cls.__module__.partition(".")[0] == "httpx" and cls.__name__ == exception_type
        for cls in type(exc).__mro__
    )


def is_http_status_error(exc: BaseException) -> bool:
    """Return whether an exception is an httpx2 or legacy-httpx status error."""
    return isinstance(exc, httpx2.HTTPStatusError) or _is_legacy_httpx_exception(
        exc, "HTTPStatusError"
    )


def get_http_status_code(exc: BaseException) -> int | None:
    """Return the response status code from a recognized HTTP status error."""
    if not is_http_status_error(exc):
        return None
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code if isinstance(status_code, int) else None


def is_timeout_error(exc: BaseException) -> bool:
    """Return whether an exception is an httpx2 or legacy-httpx timeout."""
    return isinstance(exc, httpx2.TimeoutException) or _is_legacy_httpx_exception(
        exc, "TimeoutException"
    )


def is_request_error(exc: BaseException) -> bool:
    """Return whether an exception is an httpx2 or legacy-httpx request error."""
    return isinstance(exc, httpx2.RequestError) or _is_legacy_httpx_exception(
        exc, "RequestError"
    )


def iter_exc(group: BaseExceptionGroup):
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from iter_exc(exc)
        else:
            yield exc


def _exception_handler(group: BaseExceptionGroup):
    for leaf in iter_exc(group):
        if isinstance(leaf, httpx2.ConnectTimeout):
            raise MCPError(
                code=httpx2.codes.REQUEST_TIMEOUT,
                message="Timed out while waiting for response.",
            )
        raise leaf


# this catch handler is used to catch taskgroup exception groups and raise the
# first exception. This allows more sane debugging.
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
