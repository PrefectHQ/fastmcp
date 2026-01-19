from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, overload

from mcp.server.fastmcp import FastMCP as FastMCP1Server
from pydantic import AnyUrl

from fastmcp.client.transports.base import ClientTransport, ClientTransportT
from fastmcp.client.transports.config import MCPConfigTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.client.transports.stdio import NodeStdioTransport, PythonStdioTransport
from fastmcp.mcp_config import MCPConfig, infer_transport_type_from_url
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


@overload
def infer_transport(transport: ClientTransportT) -> ClientTransportT: ...


@overload
def infer_transport(transport: FastMCP) -> FastMCPTransport: ...


@overload
def infer_transport(transport: FastMCP1Server) -> FastMCPTransport: ...


@overload
def infer_transport(transport: MCPConfig) -> MCPConfigTransport: ...


@overload
def infer_transport(transport: dict[str, Any]) -> MCPConfigTransport: ...


@overload
def infer_transport(
    transport: AnyUrl,
) -> SSETransport | StreamableHttpTransport: ...


@overload
def infer_transport(
    transport: str,
) -> (
    PythonStdioTransport | NodeStdioTransport | SSETransport | StreamableHttpTransport
): ...


@overload
def infer_transport(transport: Path) -> PythonStdioTransport | NodeStdioTransport: ...


def infer_transport(
    transport: ClientTransport
    | FastMCP
    | FastMCP1Server
    | AnyUrl
    | Path
    | MCPConfig
    | dict[str, Any]
    | str,
) -> ClientTransport:
    """
    Infer the appropriate transport type from the given transport argument.

    This function attempts to infer the correct transport type from the provided
    argument, handling various input types and converting them to the appropriate
    ClientTransport subclass.

    The function supports these input types:
    - ClientTransport: Used directly without modification
    - FastMCP or FastMCP1Server: Creates an in-memory FastMCPTransport
    - Path or str (file path): Creates PythonStdioTransport (.py) or NodeStdioTransport (.js)
    - AnyUrl or str (URL): Creates StreamableHttpTransport (default) or SSETransport (for /sse endpoints)
    - MCPConfig or dict: Creates MCPConfigTransport, potentially connecting to multiple servers

    For HTTP URLs, they are assumed to be Streamable HTTP URLs unless they end in `/sse`.

    For MCPConfig with multiple servers, a composite client is created where each server
    is mounted with its name as prefix. This allows accessing tools and resources from multiple
    servers through a single unified client interface, using naming patterns like
    `servername_toolname` for tools and `protocol://servername/path` for resources.
    If the MCPConfig contains only one server, a direct connection is established without prefixing.

    Examples:
        ```python
        # Connect to a local Python script
        transport = infer_transport("my_script.py")

        # Connect to a remote server via HTTP
        transport = infer_transport("http://example.com/mcp")

        # Connect to multiple servers using MCPConfig
        config = {
            "mcpServers": {
                "weather": {"url": "http://weather.example.com/mcp"},
                "calendar": {"url": "http://calendar.example.com/mcp"}
            }
        }
        transport = infer_transport(config)
        ```
    """

    # the transport is already a ClientTransport
    if isinstance(transport, ClientTransport):
        return transport

    # the transport is a FastMCP server (2.x or 1.0)
    elif isinstance(transport, FastMCP | FastMCP1Server):
        inferred_transport = FastMCPTransport(
            mcp=cast(FastMCP[Any] | FastMCP1Server, transport)
        )

    # the transport is a path to a script
    elif isinstance(transport, Path | str) and Path(transport).exists():
        if str(transport).endswith(".py"):
            inferred_transport = PythonStdioTransport(script_path=cast(Path, transport))
        elif str(transport).endswith(".js"):
            inferred_transport = NodeStdioTransport(script_path=cast(Path, transport))
        else:
            raise ValueError(f"Unsupported script type: {transport}")

    # the transport is an http(s) URL
    elif isinstance(transport, AnyUrl | str) and str(transport).startswith("http"):
        inferred_transport_type = infer_transport_type_from_url(
            cast(AnyUrl | str, transport)
        )
        if inferred_transport_type == "sse":
            inferred_transport = SSETransport(url=cast(AnyUrl | str, transport))
        else:
            inferred_transport = StreamableHttpTransport(
                url=cast(AnyUrl | str, transport)
            )

    # if the transport is a config dict or MCPConfig
    elif isinstance(transport, dict | MCPConfig):
        inferred_transport = MCPConfigTransport(
            config=cast(dict | MCPConfig, transport)
        )

    # the transport is an unknown type
    else:
        raise ValueError(f"Could not infer a valid transport from: {transport}")

    logger.debug(f"Inferred transport: {inferred_transport}")
    return inferred_transport
