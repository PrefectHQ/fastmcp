"""FastMCP-native server extension API (SEP-2133).

An MCP extension is an opt-in, capability-negotiated bundle of protocol
behaviour identified by a reverse-DNS string (e.g. `io.modelcontextprotocol/tasks`).
Unlike the SDK's `mcp.server.extension.Extension`, a FastMCP `ServerExtension`
is bound to its `FastMCP` instance at registration, so its request handlers and
its `tools/call` interceptor can reach the component registry, `Context`, and
auth scope that the SDK's model withholds.

An extension contributes any subset of four things:

- **A negotiated capability.** `settings()` is spliced into
  `ServerCapabilities.extensions[identifier]` (see `LowLevelServer.get_capabilities`).
- **New request methods.** `methods()` returns `MethodBinding`s, each wired onto
  the low-level server via `add_request_handler` when the extension is registered.
- **A `tools/call` interceptor.** `intercept_tool_call()` is the last gate before
  a tool body runs — it composes *after* the FastMCP middleware chain and *before*
  component execution, so it can observe, short-circuit, or pass a call through.
- **A lifespan.** `lifespan()` is entered with the server's lifespan and exited on
  shutdown — the hook the SDK's `Extension` lacks, needed to start backends/workers.

The base class follows the SDK's httpx-style shape: every contribution method has
a default, so a subclass overrides only what it needs.
"""

from __future__ import annotations

import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.extension import validate_extension_identifier
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    METHOD_NOT_FOUND,
    CallToolRequestParams,
)
from mcp_types.methods import SPEC_CLIENT_METHODS
from pydantic import BaseModel

from fastmcp.server.dependencies import _lift_meta, bind_request_context

if TYPE_CHECKING:
    import mcp_types

    from fastmcp.server.context import Context
    from fastmcp.server.server import FastMCP
    from fastmcp.tools.base import ToolResult

__all__ = [
    "MethodBinding",
    "ServerExtension",
    "read_client_extension_settings",
]

# What an extension's tools/call interceptor observes and may produce: the tool
# result, or the claimed CreateTaskResult shape when the call is run as a task.
ToolCallOutcome: TypeAlias = "ToolResult | mcp_types.CreateTaskResult"

# A method handler receives the SDK request context plus validated params and
# returns a bare result model (the runner serializes it).
ExtensionRequestHandler: TypeAlias = Callable[
    [ServerRequestContext[Any, Any], Any],
    Awaitable[BaseModel | dict[str, Any] | None],
]

# A tools/call interceptor's continuation: awaiting it runs the rest of the
# interceptor chain and, finally, the tool body.
ToolCallContinuation: TypeAlias = Callable[[], Awaitable["ToolCallOutcome"]]


@dataclass(frozen=True)
class MethodBinding:
    """A new request method an extension serves, e.g. `tasks/get`.

    `params_type` validates incoming params before `handler` runs; it should
    subclass `RequestParams` so `_meta` parses uniformly. `protocol_versions`,
    when set, restricts the method to those wire versions — a request at any
    other version is rejected as `METHOD_NOT_FOUND`, mirroring the spec's
    `(method, version)` boundary. `None` (the default) admits every version.

    Extension methods are additive: `method` must not name a spec-defined
    request method (`tools/call`, `completion/complete`, ...). Binding one would
    silently shadow the server's own handler. Both constraints are enforced at
    construction.
    """

    method: str
    params_type: type[BaseModel]
    handler: ExtensionRequestHandler
    protocol_versions: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.method in SPEC_CLIENT_METHODS:
            raise ValueError(
                f"MethodBinding cannot bind spec method {self.method!r}; extension "
                "methods are additive. Use ServerExtension.intercept_tool_call or "
                "FastMCP middleware to wrap core behaviour."
            )
        if self.protocol_versions is not None and not self.protocol_versions:
            raise ValueError(
                f"MethodBinding for {self.method!r} has an empty protocol_versions "
                "set, so it could never be served; use None to admit every version."
            )


class ServerExtension:
    """Base class for an opt-in FastMCP server extension (SEP-2133).

    Subclass, set `identifier`, and override the contribution methods that
    apply. Every method has a default, so a minimal extension overrides only
    `identifier` and one contribution. `identifier` is validated at
    subclass-definition time when set as a class attribute, and again at
    registration (which covers per-instance identifiers assigned in `__init__`).

    Register an instance with `FastMCP.add_extension(...)`, which binds the
    extension to the server so `self.server`, `intercept_tool_call`, and method
    handlers can reach FastMCP-level constructs.
    """

    #: Reverse-DNS extension identifier, advertised under `ServerCapabilities.extensions`.
    identifier: str

    _server_ref: weakref.ref[FastMCP] | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # A class-level identifier is validated here; a per-instance identifier
        # assigned in __init__ is validated at registration instead (no class
        # attribute exists to inspect at definition time).
        identifier = cls.__dict__.get("identifier")
        if identifier is not None:
            validate_extension_identifier(identifier, owner=cls.__name__)

    def _bind(self, server: FastMCP) -> None:
        """Bind this extension to its FastMCP instance (called by `add_extension`).

        A weak reference avoids a reference cycle between the server and its
        extensions. Per-instance identifiers are validated here.
        """
        validate_extension_identifier(self.identifier, owner=type(self).__name__)
        self._server_ref = weakref.ref(server)

    @property
    def server(self) -> FastMCP:
        """The FastMCP server this extension is registered on.

        Handlers, interceptors, and lifespan code reach the component registry,
        `Context`, and auth scope through here. Raises if the extension has not
        been registered with `FastMCP.add_extension()`.
        """
        ref = self._server_ref
        server = ref() if ref is not None else None
        if server is None:
            raise RuntimeError(
                f"Extension {self.identifier!r} is not bound to a FastMCP server; "
                "register it with FastMCP.add_extension() before use."
            )
        return server

    def settings(self) -> dict[str, Any]:
        """Per-extension settings advertised at `capabilities.extensions[identifier]`.

        An empty dict (the default) advertises the extension with no settings.
        """
        return {}

    def methods(self) -> Sequence[MethodBinding]:
        """New request methods this extension serves (additive)."""
        return ()

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        """A context manager entered with the server's lifespan, exited on shutdown.

        Default: a no-op. Override to start and stop resources an extension owns
        (a task-queue backend and worker, say). Entered once per runtime tree, at
        the root — a mounted child defers to the root, as the shared Docket does.
        """
        return nullcontext()

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        context: Context,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        """Wrap `tools/call`. Default: pass through unchanged.

        Runs after the FastMCP middleware chain and before the tool body, so it
        is the last gate before execution. Override to observe the call, to
        short-circuit (return a result without awaiting `call_next`), or to pass
        it through (`return await call_next()`). `params` is the validated
        `tools/call` params; `context` is the FastMCP `Context`, from which the
        tool being called (`context.fastmcp.get_tool(params.name)`), auth scope,
        and the server are reachable. Multiple extensions nest with the
        first-registered outermost.
        """
        return await call_next()

    def client_settings(
        self, ctx: ServerRequestContext[Any, Any]
    ) -> dict[str, Any] | None:
        """This extension's per-request opt-in settings declared by the client.

        Reads the request's `_meta` client-capabilities block. Returns the
        declared settings dict (possibly empty) when the client opted this
        extension in for the request, or `None` when it did not. Convenience for
        `read_client_extension_settings(ctx, self.identifier)`.
        """
        return read_client_extension_settings(ctx, self.identifier)


def _extract_client_extension_settings(
    meta: Mapping[str, Any] | None, identifier: str
) -> dict[str, Any] | None:
    """Pull `_meta[clientCapabilities][extensions][identifier]` from a lifted meta block."""
    if not meta:
        return None
    client_caps = meta.get(CLIENT_CAPABILITIES_META_KEY)
    if not isinstance(client_caps, Mapping):
        return None
    extensions = client_caps.get("extensions")
    if not isinstance(extensions, Mapping):
        return None
    settings = extensions.get(identifier)
    if isinstance(settings, Mapping):
        return dict(settings)
    return None


def read_client_extension_settings(
    ctx: ServerRequestContext[Any, Any], identifier: str
) -> dict[str, Any] | None:
    """Read a client's per-request extension opt-in from the request `_meta`.

    SEP-2133 extensions negotiate per request: the client repeats its extension
    capabilities in each request's `_meta` under
    `io.modelcontextprotocol/clientCapabilities` → `extensions` → `identifier`.
    Returns the declared settings dict (possibly empty) when the extension was
    opted in for this request, or `None` when it was not.
    """
    return _extract_client_extension_settings(_lift_meta(ctx), identifier)


def build_method_handler(binding: MethodBinding) -> ExtensionRequestHandler:
    """Wrap a `MethodBinding` into a low-level request handler.

    The adapter enforces `protocol_versions` gating (rejecting other versions as
    `METHOD_NOT_FOUND`, since `add_request_handler` registers unconditionally)
    and binds the FastMCP request context so the handler can use `get_context()`,
    auth, and other request-scoped dependencies.
    """

    async def handler(
        ctx: ServerRequestContext[Any, Any], params: Any
    ) -> BaseModel | dict[str, Any] | None:
        if (
            binding.protocol_versions is not None
            and ctx.protocol_version not in binding.protocol_versions
        ):
            raise MCPError(
                code=METHOD_NOT_FOUND,
                message=(
                    f"Method {binding.method!r} is not available at protocol "
                    f"version {ctx.protocol_version!r}."
                ),
            )
        with bind_request_context(ctx):
            return await binding.handler(ctx, params)

    return handler


def wrap_tool_call_interceptor(
    extension: ServerExtension,
    call_next: Callable[[Any], Awaitable[Any]],
) -> Callable[[Any], Awaitable[Any]]:
    """Fold one extension's `intercept_tool_call` around a middleware `call_next`.

    The returned wrapper is a FastMCP `CallNext`: it hands the extension the
    validated `tools/call` params, the FastMCP `Context`, and a zero-arg
    continuation that runs the rest of the chain and, finally, the tool body.
    """

    async def wrapped(context: Any) -> Any:
        async def cont() -> Any:
            return await call_next(context)

        return await extension.intercept_tool_call(
            context.message, context.fastmcp_context, cont
        )

    return wrapped
