"""Authorization middleware for FastMCP.

This module provides middleware-based authorization using callable auth checks.
AuthMiddleware applies auth checks globally to all components on the server.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth import require_scopes, restrict_tag
    from fastmcp.server.middleware import AuthMiddleware

    # Require specific scope for all components
    mcp = FastMCP(middleware=[
        AuthMiddleware(auth=require_scopes("api"))
    ])

    # Tag-based: components tagged "admin" require "admin" scope
    mcp = FastMCP(middleware=[
        AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"]))
    ])
    ```
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

import mcp_types as mt

from fastmcp.exceptions import AuthorizationError, InsufficientScopeError
from fastmcp.prompts.base import Prompt, PromptResult
from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.server.providers.addressing import parse_hashed_backend_name
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.authorization import (
    AuthCheck,
    AuthContext,
    run_auth_checks,
    run_auth_checks_with_shortfall,
    scope_requirements,
)
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.versions import VersionSpec, version_sort_key

logger = logging.getLogger(__name__)


def _requested_version(meta: Mapping[str, Any] | None) -> VersionSpec | None:
    # SDK v2: request `_meta` is a plain dict (the `Meta` type alias), not the
    # old `RequestParams.Meta` nested model.
    if not meta:
        return None

    fastmcp_meta = meta.get("fastmcp")
    if not isinstance(fastmcp_meta, dict):
        return None

    version = fastmcp_meta.get("version")
    if isinstance(version, str):
        return VersionSpec(eq=version)

    if isinstance(version, dict):
        gte = version.get("gte")
        lt = version.get("lt")
        eq = version.get("eq")

        if not all(value is None or isinstance(value, str) for value in (gte, lt, eq)):
            return None

        return VersionSpec(gte=gte, lt=lt, eq=eq)

    return None


ComponentT = TypeVar("ComponentT", bound=FastMCPComponent)


def _pinned_version_context(
    context: MiddlewareContext[Any], version: str
) -> MiddlewareContext[Any]:
    """Copy `context` with the request pinned to `version`.

    The pin is written into the request meta (`{"fastmcp": {"version": ...}}`,
    the shape `_version_request_meta` builds), so core routing resolves the
    pinned version instead of the highest. Any other meta the request already
    carries is preserved.
    """
    meta = dict(context.message.meta or {})
    fastmcp_meta = meta.get("fastmcp")
    if isinstance(fastmcp_meta, dict):
        fastmcp_meta = {**fastmcp_meta, "version": version}
    else:
        fastmcp_meta = {"version": version}
    meta["fastmcp"] = fastmcp_meta
    return context.copy(message=context.message.model_copy(update={"meta": meta}))


def _has_custom_typed_hook(server: Any, hook: str) -> bool:
    """Whether any non-AuthMiddleware layer customizes the typed hook `hook`.

    A fallback executes a different version than the one other middleware
    authorized: they resolve the component by name, so they saw the highest
    version, and the fallback would run a lower one they never approved.
    AuthMiddleware layers are exempt — their call policy is exactly `.auth`,
    which the filtered listing and the re-authorization of the resolved
    component already enforce — as is the base pass-through hook. Any other
    layer customizing the hook may have made a version-dependent decision the
    fallback would bypass, so the original denial stands. Request-level
    `on_request` policies authorize the request, not a version, and are not
    covered by this gate.
    """
    middleware = getattr(server, "middleware", None)
    if not isinstance(middleware, list):
        return False
    base = getattr(Middleware, hook, None)
    auth_hook = getattr(AuthMiddleware, hook, None)
    for mw in middleware:
        effective = getattr(type(mw), hook, None)
        if effective is base or effective is auth_hook:
            continue
        return True
    return False


async def _all_auth_layers_allow(
    server: Any, component: FastMCPComponent, token: Any
) -> bool:
    """Whether every AuthMiddleware layer on `server` allows `component`.

    This re-authorizes the component a lookup actually returned, not the
    listing candidate it was chosen for: when providers share a name and
    version, the lookup can return a different component than the listing
    showed (a mounted twin versus a local one), and only the returned one is
    what would execute.
    """
    middleware = getattr(server, "middleware", None)
    if not isinstance(middleware, list):
        return True
    for mw in middleware:
        if not isinstance(mw, AuthMiddleware):
            continue
        ctx = AuthContext(token=token, component=component)
        try:
            if not await run_auth_checks(mw.auth, ctx):
                return False
        except AuthorizationError:
            return False
    return True


async def _fallback_version(
    server: Any,
    denied: ComponentT,
    candidates: Sequence[ComponentT],
    resolve: Callable[[str], Awaitable[ComponentT | None]],
) -> str | None:
    """Highest version below the denied one, from the middleware-filtered listing.

    Candidates come from the listing the caller actually sees, so every
    layer's list policy — AuthMiddleware's `.auth` and any custom
    middleware's `on_list_*` filter — has already allowed them. Each candidate
    is still re-resolved by its exact version string and re-authorized:
    `VersionSpec(eq=...)` matches PEP 440-equivalent spellings (`"1"` resolves
    `"1.0"`), and providers sharing a name and version can make the lookup
    return a different component than the listing showed, so a candidate
    whose re-resolution returns a differently-spelled version, or a component
    any AuthMiddleware layer refuses, is rejected rather than running a
    component the caller was denied. Unversioned candidates have no version
    to pin, so they are skipped — the same limitation as the component-level
    fallback in `_highest_available_version`.
    """
    if denied.version is None:
        return None
    token = get_access_token()
    for candidate in sorted(candidates, key=version_sort_key, reverse=True):
        if candidate.version is None:
            continue
        if version_sort_key(candidate) >= version_sort_key(denied):
            continue
        resolved = await resolve(candidate.version)
        if resolved is None or resolved.version != candidate.version:
            continue
        if not await _all_auth_layers_allow(server, resolved, token):
            continue
        return candidate.version
    return None


async def _filtered_list(
    server: Any,
    message: Any,
    method: str,
    core_list: Callable[[], Awaitable[Sequence[ComponentT]]],
) -> Sequence[ComponentT]:
    """Run a listing's typed hooks (only) as an interior dispatch.

    The fallback discovers candidates by listing, but that listing is not a
    client request: running the whole chain would replay request-level
    admission middleware — a rate limiter would charge it as another request,
    and a denial there would mask the original authorization denial. The
    `"typed"` phase runs only the per-method list hooks, the policies that
    decide what the listing shows, and skips `on_request`/`on_message`.
    """
    from fastmcp.server.context import Context

    async with Context(fastmcp=server) as ctx:
        mw_context = MiddlewareContext(
            message=message,
            source="client",
            type="request",
            method=method,
            fastmcp_context=ctx,
        )
        return await server._run_middleware(
            mw_context,
            lambda context: core_list(),
            phase="typed",
        )


async def _resolve_resource_component(
    server: Any, uri: str, version: VersionSpec | None
) -> Resource | ResourceTemplate | None:
    """Resolve a resource URI to its concrete resource or template, if any.

    Prefab renderer resources are synthesized on demand by core `read_resource`
    rather than stored on a provider, so neither lookup can see them; mirror
    that path so the global checks run against the component core would read.
    """
    component = await server.get_resource(uri, version=version)
    if component is not None:
        return component
    component = await server.get_resource_template(uri, version=version)
    if component is not None:
        return component
    from fastmcp.server.providers.prefab_synthesis import (
        synthesize_prefab_resource_by_uri,
    )

    return await synthesize_prefab_resource_by_uri(server, uri)


class AuthMiddleware(Middleware):
    """Global authorization middleware using callable checks.

    This middleware applies auth checks to all components (tools, resources,
    prompts) on the server. It uses the same callable API as component-level
    auth checks.

    The middleware:
    - Filters tools/resources/prompts from list responses based on auth checks
    - Checks auth before tool execution, resource read, and prompt render
    - Skips all auth checks for STDIO transport (no OAuth concept)

    Args:
        auth: A single auth check function or list of check functions.
            All checks must pass for authorization to succeed (AND logic).

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.auth import require_scopes

        # Require specific scope for all components
        mcp = FastMCP(middleware=[AuthMiddleware(auth=require_scopes("api"))])

        # Multiple scopes (AND logic)
        mcp = FastMCP(middleware=[
            AuthMiddleware(auth=require_scopes("read", "api"))
        ])
        ```
    """

    def __init__(self, auth: AuthCheck | list[AuthCheck]) -> None:
        self.auth = auth

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Answer completion for a denied reference like an unknown one."""
        if context.method != "completion/complete" or await self._may_complete(context):
            return await call_next(context)
        # The result envelope differs by protocol era, so keep the one the
        # server builds and drop only the suggestions.
        result = await call_next(context)
        if isinstance(result, Mapping):
            return {**result, "completion": {"values": []}}
        return result

    async def _may_complete(self, context: MiddlewareContext[Any]) -> bool:
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return True
        message = context.message
        ref = message.get("ref") if isinstance(message, Mapping) else None
        if not isinstance(ref, Mapping) or context.fastmcp_context is None:
            return False
        ref_type = str(ref.get("type"))
        key = str(ref.get("name") if ref_type == "ref/prompt" else ref.get("uri"))
        component = await context.fastmcp_context.fastmcp._completion_target(
            ref_type, key
        )
        if component is None:
            # Unknown or already hidden; the server answers empty.
            return True
        try:
            return await run_auth_checks(
                self.auth, AuthContext(token=get_access_token(), component=component)
            )
        except AuthorizationError:
            return False

    def _chain_shortfall(
        self,
        own_missing: list[str],
        ctx: AuthContext,
        server: Any,
    ) -> list[str]:
        """Widen this middleware's shortfall to cover the rest of the chain.

        Scope requirements are commonly split across several `AuthMiddleware`
        instances (the tag-based configuration does exactly this). Raising as
        soon as the first one finds a shortfall means the middleware further in
        never runs, so a caller told only the outer scope would obtain it, retry,
        and be denied for the next — the same non-convergent loop that reporting
        a union within one middleware avoids.

        Sibling requirements are read without evaluating them: `scope_requirements`
        is a pure comparison against the token and component. Only the layers the
        request would actually reach next may contribute, so the walk starts after
        this middleware and stops at the first one whose requirements cannot be
        read — a layer holding an opaque check is an unverified gate, and anything
        at or beyond it might be unreachable for reasons that have nothing to do
        with scopes. Disclosing those requirements would leak what sits behind a
        policy the request never passed.

        Layers *before* this one are already known to have passed, so they neither
        block the walk nor contribute anything. This middleware's own shortfall
        always counts: the request demonstrably reached it.

        The result is therefore complete when the reachable chain is scope-only,
        and deliberately partial otherwise — never a disclosure past an
        unevaluated gate.
        """
        middleware = getattr(server, "middleware", None)
        if not isinstance(middleware, list):
            return own_missing

        position = next(
            (i for i, mw in enumerate(middleware) if mw is self),
            None,
        )
        if position is None:
            return own_missing

        missing = set(own_missing)
        for mw in middleware[position + 1 :]:
            if not isinstance(mw, AuthMiddleware):
                continue
            sibling = scope_requirements(mw.auth, ctx)
            if sibling is None:
                # An unverified gate. Nothing at or beyond it may contribute.
                break
            missing |= set(sibling)
        return sorted(missing)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Filter tools/list response based on auth checks."""
        tools = await call_next(context)

        # STDIO has no auth concept, skip filtering
        # Late import to avoid circular import with context.py
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return tools

        token = get_access_token()

        authorized_tools: list[Tool] = []
        for tool in tools:
            ctx = AuthContext(token=token, component=tool)
            try:
                if await run_auth_checks(self.auth, ctx):
                    authorized_tools.append(tool)
            except AuthorizationError:
                continue

        return authorized_tools

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Check auth before tool execution."""
        # STDIO has no auth concept, skip enforcement
        # Late import to avoid circular import with context.py
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return await call_next(context)

        # Get the tool being called
        tool_name = context.message.name
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            # Fail closed: deny access when context is missing
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for tool '{tool_name}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': missing context"
            )

        # get_tool returns None both when the tool does not exist and when
        # component-level auth denied access, so the two cases are
        # indistinguishable here. Keep the message ambiguous to avoid
        # disclosing existence of tools the caller is not authorized to see.
        version = _requested_version(context.message.meta)
        hashed = False
        tool = await fastmcp.fastmcp.get_tool(tool_name, version=version)
        if tool is None:
            # Core routing retries a failed display-name lookup as a hashed
            # backend name: backend tools callable by apps are addressed as
            # `<hash>_<local_name>` and resolved through `get_tool_by_hash`.
            # Mirror that path so such a tool is authorized as the same
            # component however it was addressed. `get_tool_by_hash` applies
            # the transforms, visibility, enabled state, and component auth
            # a name lookup applies, so a refused tool still resolves to None.
            parsed = parse_hashed_backend_name(tool_name)
            if parsed is not None:
                digest, local_name = parsed
                tool = await fastmcp.fastmcp.get_tool_by_hash(digest, local_name)
                hashed = tool is not None
        if tool is None:
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': "
                "not found or not authorized"
            )

        # Global auth check. A denial — raised by a check or returned with a
        # shortfall — falls back to the highest lower version the
        # middleware-filtered listing shows, so an unversioned call runs the
        # version the listing shows instead of the denied highest one. Without
        # a usable lower version the original denial stands, shortfall intact.
        token = get_access_token()
        ctx = AuthContext(token=token, component=tool)
        denied: AuthorizationError | None = None
        try:
            authorized, missing = await run_auth_checks_with_shortfall(self.auth, ctx)
        except AuthorizationError as exc:
            authorized, missing, denied = False, [], exc
        if not authorized:
            # Only an unversioned call falls back: a caller who asked for a
            # specific version and was denied gets the denial, not a silent
            # switch to another version.
            pinned = None
            if version is None:
                pinned = await self._fallback_tool_version(
                    fastmcp.fastmcp, tool, tool_name, hashed=hashed
                )
            if pinned is not None:
                return await call_next(_pinned_version_context(context, pinned))
            if denied is not None:
                raise denied
            if missing:
                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)
                raise InsufficientScopeError(
                    missing,
                    message=(
                        f"Authorization failed for tool '{tool_name}': "
                        f"insufficient scope (required: {', '.join(missing)})"
                    ),
                )
            raise AuthorizationError(
                f"Authorization failed for tool '{tool_name}': insufficient permissions"
            )

        return await call_next(context)

    async def _fallback_tool_version(
        self, server: Any, tool: Tool, tool_name: str, *, hashed: bool
    ) -> str | None:
        """Highest version below the denied tool, from the middleware-filtered listing.

        Hashed backend addresses are never fallen back: the core hashed-name
        path drops the version constraint, so a pinned fallback would still
        run the denied top version. An unversioned tool has no lower version
        to fall back to, so it returns before listing anything.
        """
        if hashed or tool.version is None:
            return None
        if _has_custom_typed_hook(server, "on_call_tool"):
            return None
        candidates = [
            t
            for t in await _filtered_list(
                server,
                mt.ListToolsRequest(method="tools/list"),
                "tools/list",
                lambda: server.list_tools(run_middleware=False),
            )
            if t.name == tool.name
        ]

        async def resolve(version: str) -> Tool | None:
            resolved = await server.get_tool(tool_name, VersionSpec(eq=version))
            if resolved is None or resolved.version != version:
                return None
            return resolved

        return await _fallback_version(server, tool, candidates, resolve)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Filter resources/list response based on auth checks."""
        resources = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return resources

        token = get_access_token()

        authorized_resources: list[Resource] = []
        for resource in resources:
            ctx = AuthContext(token=token, component=resource)
            try:
                if await run_auth_checks(self.auth, ctx):
                    authorized_resources.append(resource)
            except AuthorizationError:
                continue

        return authorized_resources

    async def check_resource_access(
        self, context: MiddlewareContext[mt.ReadResourceRequestParams]
    ) -> None:
        """Apply the resource policy shared by reads and extension discovery."""
        # STDIO has no auth concept, skip enforcement
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return

        # Get the resource being read
        uri = context.message.uri
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for resource '{uri}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': missing context"
            )

        # get_resource/get_resource_template return None both when the resource
        # does not exist and when component-level auth denied access, so the two
        # cases are indistinguishable here. Keep the message ambiguous to avoid
        # disclosing existence of resources the caller is not authorized to see.
        version = _requested_version(context.message.meta)
        component = await _resolve_resource_component(
            fastmcp.fastmcp, str(uri), version
        )
        if component is None:
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': "
                "not found or not authorized"
            )

        # Global auth check
        token = get_access_token()
        ctx = AuthContext(token=token, component=component)
        authorized, missing = await run_auth_checks_with_shortfall(self.auth, ctx)
        if not authorized:
            if missing:
                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)
                raise InsufficientScopeError(
                    missing,
                    message=(
                        f"Authorization failed for resource '{uri}': "
                        f"insufficient scope (required: {', '.join(missing)})"
                    ),
                )
            raise AuthorizationError(
                f"Authorization failed for resource '{uri}': insufficient permissions"
            )

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Authorize a resource read, falling back to the highest lower version
        the middleware-filtered listing shows when the unversioned call is
        denied for the version the listing shows. The overridable
        `check_resource_access` hook still decides: it runs against the pinned
        context too, so subclass policy is enforced on the fallback as well.
        """
        try:
            await self.check_resource_access(context)
        except AuthorizationError as exc:
            pinned = await self._fallback_resource_context(context)
            if pinned is None:
                raise
            try:
                await self._recheck_resource_access(pinned)
            except AuthorizationError:
                # The fallback is denied too; report the denial for the
                # version the caller asked for, not the fallback's.
                raise exc from None
            return await call_next(pinned)
        return await call_next(context)

    async def _recheck_resource_access(
        self, context: MiddlewareContext[mt.ReadResourceRequestParams]
    ) -> None:
        """Re-run the resource-access hooks that will not see the pinned request again.

        Every layer up to and including this one already approved the unpinned
        version, but any of them may forbid the fallback — without this, the
        fallback would run a version an earlier hook explicitly denied.
        Layers after this one see the pinned context through `call_next`, so
        they are not re-run here.
        """
        fastmcp = context.fastmcp_context
        server = fastmcp.fastmcp if fastmcp is not None else None
        middleware = getattr(server, "middleware", None)
        if not isinstance(middleware, list):
            await self.check_resource_access(context)
            return
        position = next(
            (i for i, mw in enumerate(middleware) if mw is self),
            None,
        )
        if position is None:
            await self.check_resource_access(context)
            return
        for mw in middleware[: position + 1]:
            await mw.check_resource_access(context)

    async def _fallback_resource_context(
        self, context: MiddlewareContext[mt.ReadResourceRequestParams]
    ) -> MiddlewareContext[mt.ReadResourceRequestParams] | None:
        """Pin the request to the highest lower resource version the
        middleware-filtered listing shows, or None when there is none.

        A denied concrete resource only falls back to concrete resources at
        the same URI, and a denied template only to templates with the same
        `uri_template` — never to a template that merely happens to match the
        URI, since core `read_resource` lets concrete resources shadow
        templates.
        """
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            return None
        server = fastmcp.fastmcp
        uri = str(context.message.uri)
        version = _requested_version(context.message.meta)
        # Only an unversioned call falls back: a caller who asked for a
        # specific version and was denied gets the denial, not a silent
        # switch to another version.
        if version is not None:
            return None
        if _has_custom_typed_hook(server, "on_read_resource"):
            return None
        denied = await _resolve_resource_component(server, uri, version)
        if denied is None or denied.version is None:
            return None
        if isinstance(denied, ResourceTemplate):
            candidates = [
                t
                for t in await _filtered_list(
                    server,
                    mt.ListResourceTemplatesRequest(method="resources/templates/list"),
                    "resources/templates/list",
                    lambda: server.list_resource_templates(run_middleware=False),
                )
                if t.uri_template == denied.uri_template
            ]

            async def resolve_template(version: str) -> ResourceTemplate | None:
                resolved = await server.get_resource_template(
                    uri, VersionSpec(eq=version)
                )
                if (
                    resolved is None
                    or resolved.version != version
                    or resolved.uri_template != denied.uri_template
                ):
                    return None
                return resolved

            pinned = await _fallback_version(
                server, denied, candidates, resolve_template
            )
        else:
            candidates = [
                r
                for r in await _filtered_list(
                    server,
                    mt.ListResourcesRequest(method="resources/list"),
                    "resources/list",
                    lambda: server.list_resources(run_middleware=False),
                )
                if str(r.uri) == uri
            ]

            async def resolve_resource(version: str) -> Resource | None:
                resolved = await server.get_resource(uri, VersionSpec(eq=version))
                if (
                    resolved is None
                    or resolved.version != version
                    or str(resolved.uri) != uri
                ):
                    return None
                return resolved

            pinned = await _fallback_version(
                server, denied, candidates, resolve_resource
            )
        if pinned is None:
            return None
        return _pinned_version_context(context, pinned)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        """Filter resource templates/list response based on auth checks."""
        templates = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return templates

        token = get_access_token()

        authorized_templates: list[ResourceTemplate] = []
        for template in templates:
            ctx = AuthContext(token=token, component=template)
            try:
                if await run_auth_checks(self.auth, ctx):
                    authorized_templates.append(template)
            except AuthorizationError:
                continue

        return authorized_templates

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        """Filter prompts/list response based on auth checks."""
        prompts = await call_next(context)

        # STDIO has no auth concept, skip filtering
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return prompts

        token = get_access_token()

        authorized_prompts: list[Prompt] = []
        for prompt in prompts:
            ctx = AuthContext(token=token, component=prompt)
            try:
                if await run_auth_checks(self.auth, ctx):
                    authorized_prompts.append(prompt)
            except AuthorizationError:
                continue

        return authorized_prompts

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        """Check auth before prompt render."""
        # STDIO has no auth concept, skip enforcement
        from fastmcp.server.context import _current_transport

        if _current_transport.get() == "stdio":
            return await call_next(context)

        # Get the prompt being rendered
        prompt_name = context.message.name
        fastmcp = context.fastmcp_context
        if fastmcp is None:
            logger.warning(
                f"AuthMiddleware: fastmcp_context is None for prompt '{prompt_name}'. "
                "Denying access for security."
            )
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': missing context"
            )

        # get_prompt returns None both when the prompt does not exist and when
        # component-level auth denied access, so the two cases are
        # indistinguishable here. Keep the message ambiguous to avoid
        # disclosing existence of prompts the caller is not authorized to see.
        version = _requested_version(context.message.meta)
        prompt = await fastmcp.fastmcp.get_prompt(prompt_name, version=version)
        if prompt is None:
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': "
                "not found or not authorized"
            )

        # Global auth check. A denial — raised by a check or returned with a
        # shortfall — falls back to the highest lower version the
        # middleware-filtered listing shows, so an unversioned call runs the
        # version the listing shows instead of the denied highest one. Without
        # a usable lower version the original denial stands, shortfall intact.
        token = get_access_token()
        ctx = AuthContext(token=token, component=prompt)
        denied: AuthorizationError | None = None
        try:
            authorized, missing = await run_auth_checks_with_shortfall(self.auth, ctx)
        except AuthorizationError as exc:
            authorized, missing, denied = False, [], exc
        if not authorized:
            # Only an unversioned call falls back: a caller who asked for a
            # specific version and was denied gets the denial, not a silent
            # switch to another version.
            pinned = None
            if version is None:
                pinned = await self._fallback_prompt_version(fastmcp.fastmcp, prompt)
            if pinned is not None:
                return await call_next(_pinned_version_context(context, pinned))
            if denied is not None:
                raise denied
            if missing:
                missing = self._chain_shortfall(missing, ctx, fastmcp.fastmcp)
                raise InsufficientScopeError(
                    missing,
                    message=(
                        f"Authorization failed for prompt '{prompt_name}': "
                        f"insufficient scope (required: {', '.join(missing)})"
                    ),
                )
            raise AuthorizationError(
                f"Authorization failed for prompt '{prompt_name}': insufficient permissions"
            )

        return await call_next(context)

    async def _fallback_prompt_version(self, server: Any, prompt: Prompt) -> str | None:
        """Highest version below the denied prompt, from the middleware-filtered listing.

        An unversioned prompt has no lower version to fall back to, so it
        returns before listing anything.
        """
        if prompt.version is None:
            return None
        if _has_custom_typed_hook(server, "on_get_prompt"):
            return None
        candidates = [
            p
            for p in await _filtered_list(
                server,
                mt.ListPromptsRequest(method="prompts/list"),
                "prompts/list",
                lambda: server.list_prompts(run_middleware=False),
            )
            if p.name == prompt.name
        ]

        async def resolve(version: str) -> Prompt | None:
            resolved = await server.get_prompt(prompt.name, VersionSpec(eq=version))
            if resolved is None or resolved.version != version:
                return None
            return resolved

        return await _fallback_version(server, prompt, candidates, resolve)
