import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, overload

from mcp.server.mcpserver import MCPServer as SDKServer
from pydantic import AnyUrl

from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.client.transports.base import ClientTransport, ClientTransportT
from fastmcp.client.transports.config import MCPConfigTransport
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.memory import FastMCPTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.client.transports.stdio import (
    NodeStdioTransport,
    PythonStdioTransport,
)
from fastmcp.mcp_config import MCPConfig, infer_transport_type_from_url
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP
else:
    FastMCP = Any

logger = get_logger(__name__)


_PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])


def _external_stacklevel() -> int:
    """Stack level of the first caller outside the fastmcp package.

    Public entry points reach `infer_transport` through different wrapper depths
    (`Client(...)`, `create_proxy(...)`, a direct call), so a fixed stacklevel
    would point the deprecation warning at internal frames for some of them.
    """
    level = 1
    frame = sys._getframe(1)
    while frame is not None:
        if not frame.f_code.co_filename.startswith(_PACKAGE_ROOT):
            return level
        frame = frame.f_back
        level += 1
    return 1


@overload
def infer_transport(transport: ClientTransportT) -> ClientTransportT: ...


@overload
def infer_transport(transport: FastMCP) -> FastMCPTransport: ...


@overload
def infer_transport(transport: SDKServer) -> FastMCPTransport: ...


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
    | SDKServer
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
    - FastMCP or SDKServer: Creates an in-memory FastMCPTransport
    - Path: Creates PythonStdioTransport (.py) or NodeStdioTransport (.js)
    - AnyUrl or str (URL): Creates StreamableHttpTransport (default) or SSETransport (for /sse endpoints).
      A str naming an existing .py or .js file still infers a stdio transport but
      emits a FastMCPDeprecationWarning; pass a Path instead.
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
        transport = infer_transport(Path("my_script.py"))

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
    elif _is_fastmcp_server(transport):
        inferred_transport = FastMCPTransport(
            mcp=cast("FastMCP[Any] | SDKServer", transport)
        )

    # the transport is a path to a script
    elif isinstance(transport, Path):
        if transport.suffix == ".py":
            inferred_transport = PythonStdioTransport(script_path=transport)
        elif transport.suffix == ".js":
            inferred_transport = NodeStdioTransport(script_path=transport)
        else:
            raise ValueError(f"Unsupported script type: {transport}")

    # a string naming an existing script: still works, but warns. Strings will
    # mean URLs only in FastMCP 5; a Path is the explicit way to run a script.
    elif (
        isinstance(transport, str)
        and transport.endswith((".py", ".js"))
        and Path(transport).exists()
    ):
        warnings.warn(
            f"Inferring a stdio transport from the string {transport!r} is"
            " deprecated and will be removed in FastMCP 5. Pass"
            f" pathlib.Path({transport!r}) or an explicit StdioTransport instead.",
            FastMCPDeprecationWarning,
            stacklevel=_external_stacklevel(),
        )
        inferred_transport = infer_transport(Path(transport))

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


def _is_fastmcp_server(transport: object) -> bool:
    if isinstance(transport, SDKServer):
        return True

    try:
        from fastmcp.server.server import FastMCP as FastMCP2Server
    except ImportError:
        return False

    return isinstance(transport, FastMCP2Server)
