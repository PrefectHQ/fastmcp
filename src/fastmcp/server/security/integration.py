"""SecureMCP integration helpers for FastMCP servers.

These helpers keep SecureMCP wiring outside FastMCP core so security can be
attached through public server hooks instead of constructor patches.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from fastmcp.server.security.config import SecurityConfig
from fastmcp.server.security.orchestrator import SecurityContext, SecurityOrchestrator
from fastmcp.server.security.settings import SecuritySettings, get_security_settings
from fastmcp.tools.tool import Tool

if TYPE_CHECKING:
    from fastmcp import FastMCP


_ATTACHED_SECURITY_CONTEXTS: WeakKeyDictionary[FastMCP, SecurityContext] = (
    WeakKeyDictionary()
)
_REGISTERED_GATEWAY_TOOLS: WeakKeyDictionary[FastMCP, set[str]] = WeakKeyDictionary()


def get_security_context(server: FastMCP) -> SecurityContext | None:
    """Return the SecureMCP context for a server if one is attached."""

    return _ATTACHED_SECURITY_CONTEXTS.get(server)


def attach_security_context(
    server: FastMCP,
    context: SecurityContext,
    *,
    register_gateway_tools: bool = False,
) -> SecurityContext:
    """Attach an existing SecurityContext to a FastMCP server.

    This attaches middleware through the server's public hook and records the
    context in an external registry, keeping SecureMCP state out of core-owned
    server fields.
    """

    existing = get_security_context(server)
    if existing is not None:
        raise RuntimeError(f"SecureMCP is already attached to server {server.name!r}")

    for middleware in context.middleware:
        server.add_middleware(middleware)

    _ATTACHED_SECURITY_CONTEXTS[server] = context

    if register_gateway_tools:
        register_security_gateway_tools(server, context=context)

    return context


def attach_security(
    server: FastMCP,
    config: SecurityConfig,
    *,
    bypass_stdio: bool | None = None,
    settings: SecuritySettings | None = None,
    register_gateway_tools: bool = False,
) -> SecurityContext:
    """Bootstrap SecureMCP from config and attach it to a FastMCP server."""

    resolved_settings = settings or get_security_settings()
    effective_config = (
        config if resolved_settings.enabled else replace(config, enabled=False)
    )
    effective_bypass_stdio = (
        resolved_settings.policy_bypass_stdio if bypass_stdio is None else bypass_stdio
    )

    context = SecurityOrchestrator.bootstrap(
        effective_config,
        server_name=server.name,
        bypass_stdio=effective_bypass_stdio,
    )
    return attach_security_context(
        server,
        context,
        register_gateway_tools=register_gateway_tools,
    )


def register_security_gateway_tools(
    server: FastMCP,
    *,
    context: SecurityContext | None = None,
) -> list[str]:
    """Register SecureMCP gateway tools on a FastMCP server.

    Gateway tools are registered explicitly so the extension remains additive
    and opt-in.
    """

    context = context or get_security_context(server)
    if context is None:
        raise RuntimeError(
            f"No SecureMCP context is attached to server {server.name!r}"
        )

    added = _REGISTERED_GATEWAY_TOOLS.get(server)
    if added is None:
        added = set()
        _REGISTERED_GATEWAY_TOOLS[server] = added
    registered_now: list[str] = []

    for name, fn in context.gateway_tools.items():
        if name in added:
            continue
        server.add_tool(Tool.from_function(fn, name=name))
        added.add(name)
        registered_now.append(name)

    return registered_now
