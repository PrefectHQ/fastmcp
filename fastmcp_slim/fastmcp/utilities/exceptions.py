from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx2
from exceptiongroup import BaseExceptionGroup
from mcp import MCPError

import fastmcp

# FastMCP uses httpx2 internally, but user-supplied code (tools, resources, and
# clients handed to the OpenAPI integration) may still raise exceptions from the
# legacy httpx package. These catch tuples include both families when httpx is
# installed, so user errors keep their specific handling without making httpx a
# FastMCP dependency. The two libraries' exception hierarchies match name-for-name.
try:
    import httpx

    HTTP_STATUS_ERRORS: tuple[type[BaseException], ...] = (
        httpx2.HTTPStatusError,
        httpx.HTTPStatusError,
    )
    TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
        httpx2.TimeoutException,
        httpx.TimeoutException,
    )
    REQUEST_ERRORS: tuple[type[BaseException], ...] = (
        httpx2.RequestError,
        httpx.RequestError,
    )
except ImportError:
    HTTP_STATUS_ERRORS = (httpx2.HTTPStatusError,)
    TIMEOUT_ERRORS = (httpx2.TimeoutException,)
    REQUEST_ERRORS = (httpx2.RequestError,)


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
