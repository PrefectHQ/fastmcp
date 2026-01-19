# Re-export all public APIs for backward compatibility
from mcp.server.fastmcp import FastMCP as FastMCP1Server

from fastmcp.client.transports.base import (
    ClientTransport,
    ClientTransportT,
    SessionKwargs,
)
from fastmcp.client.transports.config import MCPConfigTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.inference import infer_transport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.client.transports.stdio import (
    FastMCPStdioTransport,
    NodeStdioTransport,
    NpxStdioTransport,
    PythonStdioTransport,
    StdioTransport,
    UvStdioTransport,
    UvxStdioTransport,
)
from fastmcp.server.server import FastMCP

__all__ = [
    "ClientTransport",
    "FastMCPStdioTransport",
    "FastMCPTransport",
    "NodeStdioTransport",
    "NpxStdioTransport",
    "PythonStdioTransport",
    "SSETransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "UvStdioTransport",
    "UvxStdioTransport",
    "infer_transport",
]
