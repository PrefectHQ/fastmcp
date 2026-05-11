try:
    from .auth import OAuth, BearerAuth
    from .client import Client
    from .transports import (
        ClientTransport,
        FastMCPTransport,
        NodeStdioTransport,
        NpxStdioTransport,
        PythonStdioTransport,
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
        UvStdioTransport,
        UvxStdioTransport,
    )
except ImportError as exc:
    raise ImportError(
        "FastMCP client support is not installed. Install "
        "`fastmcp-slim[client]` or `fastmcp`."
    ) from exc

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
