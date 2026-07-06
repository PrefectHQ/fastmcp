import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias, cast

import mcp_types
import pydantic
from mcp import ClientSession
from mcp.client.session import ClientRequestContext, ListRootsFnT

from fastmcp.client._sdk_context_shim import LifespanContextT, RequestContext

RootsList: TypeAlias = list[str] | list[mcp_types.Root] | list[str | mcp_types.Root]

RootsHandler: TypeAlias = (
    Callable[[RequestContext[ClientSession, LifespanContextT]], RootsList]
    | Callable[[RequestContext[ClientSession, LifespanContextT]], Awaitable[RootsList]]
)


def convert_roots_list(roots: RootsList) -> list[mcp_types.Root]:
    roots_list = []
    for r in roots:
        if isinstance(r, mcp_types.Root):
            roots_list.append(r)
        elif isinstance(r, pydantic.FileUrl):
            roots_list.append(mcp_types.Root(uri=r))
        elif isinstance(r, str):
            roots_list.append(mcp_types.Root(uri=pydantic.FileUrl(r)))
        else:
            raise ValueError(f"Invalid root: {r}")
    return roots_list


def create_roots_callback(
    handler: RootsList | RootsHandler,
) -> ListRootsFnT:
    if isinstance(handler, list):
        # TODO(ty): remove when ty supports isinstance union narrowing
        return _create_roots_callback_from_roots(handler)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
    elif inspect.isfunction(handler):
        return _create_roots_callback_from_fn(handler)
    else:
        raise ValueError(f"Invalid roots handler: {handler}")


def _create_roots_callback_from_roots(
    roots: RootsList,
) -> ListRootsFnT:
    roots = convert_roots_list(roots)

    async def _roots_callback(
        context: ClientRequestContext,
    ) -> mcp_types.ListRootsResult:
        return mcp_types.ListRootsResult(roots=roots)

    return _roots_callback


def _create_roots_callback_from_fn(
    fn: Callable[[RequestContext[ClientSession, LifespanContextT]], RootsList]
    | Callable[[RequestContext[ClientSession, LifespanContextT]], Awaitable[RootsList]],
) -> ListRootsFnT:
    async def _roots_callback(
        context: ClientRequestContext,
    ) -> mcp_types.ListRootsResult | mcp_types.ErrorData:
        try:
            # The public RootsHandler alias is typed against the subscriptable
            # RequestContext shim; the runtime object is the SDK's
            # ClientRequestContext, passed through opaquely.
            roots = fn(context)  # ty: ignore[invalid-argument-type]
            if inspect.isawaitable(roots):
                roots = await roots
            return mcp_types.ListRootsResult(
                roots=convert_roots_list(cast(RootsList, roots))
            )
        except Exception as e:
            return mcp_types.ErrorData(
                code=mcp_types.INTERNAL_ERROR,
                message=str(e),
            )

    return _roots_callback
