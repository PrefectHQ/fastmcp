"""Pre-dispatch HTTP challenges for component scope requirements.

The MCP transport commits SSE response headers before dispatching a request.
Scope checks that need an HTTP 403 must therefore run before entering it.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


class ScopeChallengeMiddleware:
    """Challenge a known scope shortfall before the HTTP response starts.

    Only scope-only authorization chains can be classified without running
    user policy twice. Opaque middleware and malformed requests retain the
    ordinary MCP dispatch path and its existing error behavior.
    """

    def __init__(self, app: ASGIApp, server: FastMCP[Any]) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].lower()
            != "application/json"
        ):
            await self.app(scope, receive, send)
            return
        if not await self._can_preflight(scope, request):
            await self.app(scope, receive, send)
            return

        # Read only a small request before the SDK's own 4 MiB body limiter.
        # Larger bodies are replayed to the SDK, which owns the 413 response.
        buffered: deque[Message] = deque()
        chunks: list[bytes] = []
        size = 0
        complete = False
        while size <= 64 * 1024:
            event = await receive()
            buffered.append(event)
            if event["type"] != "http.request":
                break
            chunk = event.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            if not event.get("more_body", False):
                complete = True
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            return await receive()

        if not complete or size > 64 * 1024:
            await self.app(scope, replay_receive, send)
            return

        body = b"".join(chunks)

        try:
            message = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            message = None

        missing = await self._missing_scopes(message, request)
        if missing:
            response = Response(
                status_code=403,
                headers={
                    "WWW-Authenticate": (
                        'Bearer error="insufficient_scope", '
                        f'scope="{" ".join(missing)}"'
                    )
                },
            )
            await response(scope, replay_receive, send)
            return

        await self.app(scope, replay_receive, send)

    async def _can_preflight(self, scope: Scope, request: Request) -> bool:
        from mcp.server.auth.middleware.bearer_auth import (
            AuthenticatedUser,
            authorization_context,
        )
        from mcp.server.streamable_http import check_accept_headers
        from mcp.shared.inbound import MODERN_PROTOCOL_VERSIONS
        from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

        manager = getattr(self.app, "session_manager", None)
        if manager is None:
            return False
        accepts_json, accepts_sse = check_accept_headers(request)
        if not accepts_json or (not manager.json_response and not accepts_sse):
            return False
        protocol = request.headers.get("mcp-protocol-version")
        if protocol is not None and protocol not in HANDSHAKE_PROTOCOL_VERSIONS:
            # The modern transport is sessionless. Unknown versions must reach
            # its protocol validator instead of receiving a scope challenge.
            return protocol in MODERN_PROTOCOL_VERSIONS
        if manager.stateless:
            return True

        session_id = request.headers.get("mcp-session-id")
        if session_id is None or session_id not in manager._server_instances:
            return False
        user = scope.get("user")
        owner = (
            authorization_context(user) if isinstance(user, AuthenticatedUser) else None
        )
        if owner != manager._session_owners.get(session_id):
            return False

        # Session visibility can hide or re-enable a component. Without the
        # request's Context, preflight cannot apply those rules accurately.
        try:
            rules = await self.server._state_store.get(
                key=f"{session_id}:_visibility_rules"
            )
        except Exception:
            return False
        return rules is None or not rules.value

    async def _missing_scopes(self, message: Any, request: Request) -> list[str]:
        from mcp.shared.inbound import (
            MODERN_PROTOCOL_VERSIONS,
            InboundLadderRejection,
            classify_inbound_request,
            find_duplicated_routing_header,
            x_mcp_header_map,
        )

        from fastmcp.server.dependencies import get_access_token
        from fastmcp.server.middleware.authorization import (
            AuthMiddleware,
            _requested_version,
        )
        from fastmcp.server.middleware.middleware import Middleware
        from fastmcp.server.transforms.visibility import is_enabled
        from fastmcp.tools.base import Tool
        from fastmcp.utilities.authorization import AuthContext, scope_requirements

        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("id"), (str, int))
            or isinstance(message["id"], bool)
        ):
            return []
        method = message.get("method")
        params = message.get("params")
        if method not in (
            "tools/call",
            "resources/read",
            "prompts/get",
        ) or not isinstance(params, dict):
            return []
        meta = params.get("_meta")
        body_version = (
            meta.get("io.modelcontextprotocol/protocolVersion")
            if isinstance(meta, dict)
            else None
        )
        if (
            request.headers.get("mcp-protocol-version") in MODERN_PROTOCOL_VERSIONS
            or body_version in MODERN_PROTOCOL_VERSIONS
        ):
            if any(
                key.lower().startswith("mcp-param-")
                for key, _ in request.headers.items()
            ):
                return []
            if find_duplicated_routing_header(request.headers.items()) is not None:
                return []
            verdict = classify_inbound_request(
                message,
                headers={key.lower(): value for key, value in request.headers.items()},
            )
            if isinstance(verdict, InboundLadderRejection):
                return []

        # A token has already been validated by RequireAuthMiddleware, which
        # wraps this app. Missing authentication is a 401, not a scope challenge.
        token = get_access_token()
        if token is None:
            return []

        # Only local components without transforms or per-component policy are
        # safe to inspect here. Other providers and checks may have side effects
        # or differ from the eventual request's session context.
        provider = self.server.local_provider
        if (
            len(self.server.providers) != 1
            or self.server.providers[0] is not provider
            or self.server.transforms
            or provider.transforms
        ):
            return []

        for middleware in self.server.middleware:
            if not isinstance(middleware, AuthMiddleware):
                handlers = (
                    "on_message",
                    "on_request",
                    {
                        "tools/call": "on_call_tool",
                        "resources/read": "on_read_resource",
                        "prompts/get": "on_get_prompt",
                    }[method],
                )
                if any(
                    getattr(type(middleware), name) is not getattr(Middleware, name)
                    for name in handlers
                ):
                    return []

        version = _requested_version(meta)
        try:
            if method == "tools/call" and isinstance(params.get("name"), str):
                component = await provider.get_tool(params["name"], version=version)
            elif method == "resources/read" and isinstance(params.get("uri"), str):
                component = await provider.get_resource(params["uri"], version=version)
                if component is None:
                    component = await provider.get_resource_template(
                        params["uri"], version=version
                    )
            elif method == "prompts/get" and isinstance(params.get("name"), str):
                component = await provider.get_prompt(params["name"], version=version)
            else:
                return []
        except Exception:
            # The normal dispatch path owns provider errors and their MCP
            # translation. A failed preflight must not replace that response.
            return []

        if component is None or component.auth is not None or not is_enabled(component):
            return []
        if isinstance(component, Tool):
            try:
                if x_mcp_header_map(component.parameters):
                    return []
            except Exception:
                return []

        context = AuthContext(token=token, component=component)
        missing: set[str] = set()
        for middleware in self.server.middleware:
            if isinstance(middleware, AuthMiddleware):
                required = scope_requirements(middleware.auth, context)
                if required is None:
                    return []
                missing.update(required)
        return sorted(missing)
