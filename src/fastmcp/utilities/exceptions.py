from collections.abc import Callable, Iterable, Mapping
from typing import Any

from exceptiongroup import BaseExceptionGroup

import fastmcp


def iter_exc(group: BaseExceptionGroup):
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from iter_exc(exc)
        else:
            yield exc


def _exception_handler(group: BaseExceptionGroup):
    for leaf in iter_exc(group):
        raise leaf


# this catch handler is used to catch taskgroup exception groups and raise the
# first exception. This allows more sane debugging.
catch_handlers: Mapping[
    type[BaseException] | Iterable[type[BaseException]],
    Callable[[BaseExceptionGroup[Any]], Any],
] = {
    Exception: _exception_handler,
}


def get_catch_handlers() -> Mapping[
    type[BaseException] | Iterable[type[BaseException]],
    Callable[[BaseExceptionGroup[Any]], Any],
]:
    if fastmcp.settings.settings.client_raise_first_exceptiongroup_error:
        return catch_handlers
    else:
        return {}
