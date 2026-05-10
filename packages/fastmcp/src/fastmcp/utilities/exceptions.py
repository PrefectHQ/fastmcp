from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx
import mcp.types
from exceptiongroup import BaseExceptionGroup
from mcp import McpError

import fastmcp


def iter_exc(group: BaseExceptionGroup):
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from iter_exc(exc)
        else:
            yield exc


def _exception_handler(group: BaseExceptionGroup):
    for leaf in iter_exc(group):
        if isinstance(leaf, httpx.ConnectTimeout):
            raise McpError(
                error=mcp.types.ErrorData(
                    code=httpx.codes.REQUEST_TIMEOUT,
                    message="Timed out while waiting for response.",
                )
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
