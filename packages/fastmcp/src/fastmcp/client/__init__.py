"""Compatibility imports for the client package.

The client implementation lives in ``fastmcp_client``. The full ``fastmcp``
package keeps the historical ``fastmcp.client`` import paths by aliasing them
to the split-out package.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, cast

import fastmcp as _fastmcp
import fastmcp_client as _fastmcp_client

cast(Any, _fastmcp_client).settings = _fastmcp.settings

_MODULE_ALIASES = {
    "fastmcp.client.auth": "fastmcp_client.client.auth",
    "fastmcp.client.auth.bearer": "fastmcp_client.client.auth.bearer",
    "fastmcp.client.auth.oauth": "fastmcp_client.client.auth.oauth",
    "fastmcp.client.client": "fastmcp_client.client.client",
    "fastmcp.client.elicitation": "fastmcp_client.client.elicitation",
    "fastmcp.client.logging": "fastmcp_client.client.logging",
    "fastmcp.client.messages": "fastmcp_client.client.messages",
    "fastmcp.client.mixins": "fastmcp_client.client.mixins",
    "fastmcp.client.mixins.prompts": "fastmcp_client.client.mixins.prompts",
    "fastmcp.client.mixins.resources": "fastmcp_client.client.mixins.resources",
    "fastmcp.client.mixins.task_management": "fastmcp_client.client.mixins.task_management",
    "fastmcp.client.mixins.tools": "fastmcp_client.client.mixins.tools",
    "fastmcp.client.oauth_callback": "fastmcp_client.client.oauth_callback",
    "fastmcp.client.progress": "fastmcp_client.client.progress",
    "fastmcp.client.roots": "fastmcp_client.client.roots",
    "fastmcp.client.tasks": "fastmcp_client.client.tasks",
    "fastmcp.client.telemetry": "fastmcp_client.client.telemetry",
    "fastmcp.client.transports": "fastmcp_client.client.transports",
    "fastmcp.client.transports.base": "fastmcp_client.client.transports.base",
    "fastmcp.client.transports.config": "fastmcp_client.client.transports.config",
    "fastmcp.client.transports.http": "fastmcp_client.client.transports.http",
    "fastmcp.client.transports.inference": "fastmcp_client.client.transports.inference",
    "fastmcp.client.transports.memory": "fastmcp_client.client.transports.memory",
    "fastmcp.client.transports.sse": "fastmcp_client.client.transports.sse",
    "fastmcp.client.transports.stdio": "fastmcp_client.client.transports.stdio",
}


def _alias_module(alias: str, target: str) -> None:
    sys.modules[alias] = importlib.import_module(target)


for _alias, _target in _MODULE_ALIASES.items():
    _alias_module(_alias, _target)

from fastmcp_client.client import (  # noqa: E402
    BearerAuth,
    Client,
    ClientTransport,
    FastMCPTransport,
    NodeStdioTransport,
    NpxStdioTransport,
    OAuth,
    PythonStdioTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
    UvStdioTransport,
    UvxStdioTransport,
)

__all__ = [
    "BearerAuth",
    "Client",
    "ClientTransport",
    "FastMCPTransport",
    "NodeStdioTransport",
    "NpxStdioTransport",
    "OAuth",
    "PythonStdioTransport",
    "SSETransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "UvStdioTransport",
    "UvxStdioTransport",
]
