import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import mcp.types
import pydantic
from mcp import ClientSession
from mcp.client.session import ListRootsFnT
from mcp.shared.context import LifespanContextT, RequestContext

RootsList: TypeAlias = list[str] | list[mcp.types.Root] | list[str | mcp.types.Root]

RootsHandler: TypeAlias = (
    Callable[[RequestContext[ClientSession, LifespanContextT]], RootsList]
    | Callable[[RequestContext[ClientSession, LifespanContextT]], Awaitable[RootsList]]
)


def convert_roots_list(roots: RootsList) -> list[mcp.types.Root]:
    roots_list = []
    for r in roots:
        if isinstance(r, mcp.types.Root):
            roots_list.append(r)
        elif isinstance(r, pydantic.FileUrl):
            roots_list.append(mcp.types.Root(uri=r))
        elif isinstance(r, str):
            roots_list.append(mcp.types.Root(uri=pydantic.FileUrl(r)))
        else:
            raise ValueError(f"Invalid root: {r}")
    return roots_list


def create_roots_callback(
    handler: RootsList | RootsHandler,
) -> ListRootsFnT:
    if isinstance(handler, list):
        return _create_roots_callback_from_roots(handler)
    elif inspect.isfunction(handler):
        return _create_roots_callback_from_fn(handler)
    else:
        raise ValueError(f"Invalid roots handler: {handler}")


def _create_roots_callback_from_roots(
    roots: RootsList,
) -> ListRootsFnT:
    roots = convert_roots_list(roots)

    async def _roots_callback(
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> mcp.types.ListRootsResult:
        return mcp.types.ListRootsResult(roots=roots)

    return _roots_callback


def _create_roots_callback_from_fn(
    fn: Callable[[RequestContext[ClientSession, LifespanContextT]], RootsList]
    | Callable[[RequestContext[ClientSession, LifespanContextT]], Awaitable[RootsList]],
) -> ListRootsFnT:
    async def _roots_callback(
        context: RequestContext[ClientSession, LifespanContextT],
    ) -> mcp.types.ListRootsResult | mcp.types.ErrorData:
        try:
            roots = fn(context)
            if inspect.isawaitable(roots):
                roots = await roots
            return mcp.types.ListRootsResult(roots=convert_roots_list(roots))
        except Exception as e:
            return mcp.types.ErrorData(
                code=mcp.types.INTERNAL_ERROR,
                message=str(e),
            )

    return _roots_callback
