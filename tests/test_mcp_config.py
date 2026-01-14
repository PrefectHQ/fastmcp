import asyncio
import gc
import inspect
import logging
import os
import sys
import tempfile
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import psutil
import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.auth.oauth import OAuthClientProvider
from fastmcp.client.client import Client
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import (
    MCPConfigTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.mcp_config import (
    CanonicalMCPConfig,
    CanonicalMCPServerTypes,
    MCPConfig,
    MCPServerTypes,
    RemoteMCPServer,
    StdioMCPServer,
    TransformingStdioMCPServer,
)
from fastmcp.tools.tool import Tool as FastMCPTool

# Skip all tests in this file on Windows - they spawn subprocess servers via stdio
# which has process lifecycle issues on Windows
pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win32"),
    reason="Windows has process lifecycle issues with stdio subprocesses",
)


def running_under_debugger():
    return os.environ.get("DEBUGPY_RUNNING") == "true"


def gc_collect_harder():
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()


def test_parse_single_stdio_config():
    config = {
        "mcpServers": {
            "test_server": {
                "command": "echo",
                "args": ["hello"],
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.command == "echo"
    assert transport.args == ["hello"]


def test_stdio_config_keep_alive_passthrough():
    """Test that keep_alive parameter is passed through from StdioMCPServer to StdioTransport."""
    # Test with keep_alive=False
    server = StdioMCPServer(command="test", keep_alive=False)
    assert server.keep_alive is False
    transport = server.to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.keep_alive is False

    # Test with keep_alive=True
    server = StdioMCPServer(command="test", keep_alive=True)
    assert server.keep_alive is True
    transport = server.to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.keep_alive is True

    # Test with keep_alive=None (should default to True in StdioTransport)
    server = StdioMCPServer(command="test", keep_alive=None)
    assert server.keep_alive is None
    transport = server.to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.keep_alive is True  # StdioTransport defaults to True

    # Test with keep_alive not specified (should default to None, then True in StdioTransport)
    server = StdioMCPServer(command="test")
    assert server.keep_alive is None
    transport = server.to_transport()
    assert isinstance(transport, StdioTransport)
    assert transport.keep_alive is True  # StdioTransport defaults to True


def test_parse_extra_keys():
    config = {
        "mcpServers": {
            "test_server": {
                "command": "echo",
                "args": ["hello"],
                "leaf_extra": "leaf_extra",
            }
        },
        "root_extra": "root_extra",
    }
    mcp_config = MCPConfig.from_dict(config)

    serialized_mcp_config = mcp_config.to_dict()
    assert serialized_mcp_config["root_extra"] == "root_extra"
    assert (
        serialized_mcp_config["mcpServers"]["test_server"]["leaf_extra"] == "leaf_extra"
    )


def test_parse_mcpservers_at_root():
    config = {
        "test_server": {
            "command": "echo",
            "args": ["hello"],
        }
    }

    mcp_config = MCPConfig.from_dict(config)

    serialized_mcp_config = mcp_config.model_dump()
    assert serialized_mcp_config["mcpServers"]["test_server"]["command"] == "echo"
    assert serialized_mcp_config["mcpServers"]["test_server"]["args"] == ["hello"]


def test_parse_mcpservers_discriminator():
    """Test that the MCPConfig discriminator produces StdioMCPServer for a non-transforming server
    and TransformingStdioMCPServer for a transforming server."""

    config = {
        "test_server": {
            "command": "echo",
            "args": ["hello"],
        },
        "test_server_two": {"command": "echo", "args": ["hello"], "tools": {}},
    }

    mcp_config = MCPConfig.from_dict(config)

    test_server: MCPServerTypes = mcp_config.mcpServers["test_server"]
    assert isinstance(test_server, StdioMCPServer)

    test_server_two: MCPServerTypes = mcp_config.mcpServers["test_server_two"]
    assert isinstance(test_server_two, TransformingStdioMCPServer)

    canonical_mcp_config = CanonicalMCPConfig.from_dict(config)

    canonical_test_server: CanonicalMCPServerTypes = canonical_mcp_config.mcpServers[
        "test_server"
    ]
    assert isinstance(canonical_test_server, StdioMCPServer)

    canonical_test_server_two: CanonicalMCPServerTypes = (
        canonical_mcp_config.mcpServers["test_server_two"]
    )
    assert isinstance(canonical_test_server_two, StdioMCPServer)


def test_parse_single_remote_config():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.url == "http://localhost:8000"


def test_parse_remote_config_with_transport():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
                "transport": "sse",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, SSETransport)
    assert transport.url == "http://localhost:8000"


def test_parse_remote_config_with_url_inference():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000/sse/",
            }
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    transport = mcp_config.mcpServers["test_server"].to_transport()
    assert isinstance(transport, SSETransport)
    assert transport.url == "http://localhost:8000/sse/"


def test_parse_multiple_servers():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000/sse/",
            },
            "test_server_2": {
                "command": "echo",
                "args": ["hello"],
                "env": {"TEST": "test"},
            },
        }
    }
    mcp_config = MCPConfig.from_dict(config)
    assert len(mcp_config.mcpServers) == 2
    assert isinstance(mcp_config.mcpServers["test_server"], RemoteMCPServer)
    assert isinstance(mcp_config.mcpServers["test_server"].to_transport(), SSETransport)

    assert isinstance(mcp_config.mcpServers["test_server_2"], StdioMCPServer)
    assert isinstance(
        mcp_config.mcpServers["test_server_2"].to_transport(), StdioTransport
    )
    assert mcp_config.mcpServers["test_server_2"].command == "echo"
    assert mcp_config.mcpServers["test_server_2"].args == ["hello"]
    assert mcp_config.mcpServers["test_server_2"].env == {"TEST": "test"}


async def test_multi_client(tmp_path: Path):
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    client = Client(config)

    async with client:
        tools = await client.list_tools()
        assert len(tools) == 2

        result_1 = await client.call_tool("test_1_add", {"a": 1, "b": 2})
        result_2 = await client.call_tool("test_2_add", {"a": 1, "b": 2})
        assert result_1.data == 3
        assert result_2.data == 3


async def test_multi_client_parallel_calls(tmp_path: Path):
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    client = Client(config)

    async with client:
        _ = await client.list_tools()

        tasks = [client.list_tools() for _ in range(40)]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        exceptions = [result for result in results if isinstance(result, Exception)]
        assert len(exceptions) == 0
        assert len(results) == 40
        assert all(len(result) == 2 for result in results)  # type: ignore[arg-type]


@pytest.mark.skipif(
    running_under_debugger(),
    reason="Debugger holds a reference to the transport",
)
@pytest.mark.timeout(5)
async def test_multi_client_lifespan(tmp_path: Path):
    pid_1: int | None = None
    pid_2: int | None = None

    async def test_server():
        server_script = inspect.cleandoc("""
            from fastmcp import FastMCP
            import os

            mcp = FastMCP()

            @mcp.tool
            def pid() -> int:
                return os.getpid()

            if __name__ == '__main__':
                mcp.run()
            """)

        script_path = tmp_path / "test.py"
        script_path.write_text(server_script)

        config = {
            "mcpServers": {
                "test_1": {
                    "command": "python",
                    "args": [str(script_path)],
                },
                "test_2": {
                    "command": "python",
                    "args": [str(script_path)],
                },
            }
        }
        transport = MCPConfigTransport(config)
        client = Client(transport)

        async with client:
            nonlocal pid_1
            pid_1 = (await client.call_tool("test_1_pid")).data

            nonlocal pid_2
            pid_2 = (await client.call_tool("test_2_pid")).data

    await test_server()

    gc_collect_harder()

    # This test will fail while debugging because the debugger holds a reference to the underlying transport

    with pytest.raises(psutil.NoSuchProcess):
        while True:
            psutil.Process(pid_1)
            await asyncio.sleep(0.01)

    with pytest.raises(psutil.NoSuchProcess):
        while True:
            psutil.Process(pid_2)
            await asyncio.sleep(0.01)


async def test_multi_client_force_close(tmp_path: Path):
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP
        import os

        mcp = FastMCP()

        @mcp.tool
        def pid() -> int:
            return os.getpid()

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }
    transport = MCPConfigTransport(config)
    client = Client(transport)

    async with client:
        pid_1 = (await client.call_tool("test_1_pid")).data
        pid_2 = (await client.call_tool("test_2_pid")).data

    await client.close()

    gc_collect_harder()

    with pytest.raises(psutil.NoSuchProcess):
        process = psutil.Process(pid_1)

        assert not process

    with pytest.raises(psutil.NoSuchProcess):
        process = psutil.Process(pid_2)

        assert not process


async def test_remote_config_default_no_auth():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
            }
        }
    }
    client = Client(config)
    assert isinstance(client.transport.transport, StreamableHttpTransport)
    assert client.transport.transport.auth is None


async def test_remote_config_with_auth_token():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
                "auth": "test_token",
            }
        }
    }
    client = Client(config)
    assert isinstance(client.transport.transport, StreamableHttpTransport)
    assert isinstance(client.transport.transport.auth, BearerAuth)
    assert client.transport.transport.auth.token.get_secret_value() == "test_token"


async def test_remote_config_sse_with_auth_token():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000/sse/",
                "auth": "test_token",
            }
        }
    }
    client = Client(config)
    assert isinstance(client.transport.transport, SSETransport)
    assert isinstance(client.transport.transport.auth, BearerAuth)
    assert client.transport.transport.auth.token.get_secret_value() == "test_token"


async def test_remote_config_with_oauth_literal():
    config = {
        "mcpServers": {
            "test_server": {
                "url": "http://localhost:8000",
                "auth": "oauth",
            }
        }
    }
    client = Client(config)
    assert isinstance(client.transport.transport, StreamableHttpTransport)
    assert isinstance(client.transport.transport.auth, OAuthClientProvider)


async def test_multi_client_with_logging(tmp_path: Path, caplog):
    """
    Tests that logging is properly forwarded to the ultimate client.
    """
    caplog.set_level(logging.INFO, logger=__name__)

    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP, Context

        mcp = FastMCP()

        @mcp.tool
        async def log_test(message: str, ctx: Context) -> int:
            await ctx.log(message)
            return 42

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_server": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_server_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    MESSAGES = []

    logger = logging.getLogger(__name__)
    # Backwards-compatible way to get the log level mapping
    if hasattr(logging, "getLevelNamesMapping"):
        # For Python 3.11+
        LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()  # pyright: ignore [reportAttributeAccessIssue]
    else:
        # For older Python versions
        LOGGING_LEVEL_MAP = logging._nameToLevel

    async def log_handler(message: LogMessage):
        MESSAGES.append(message)

        level = LOGGING_LEVEL_MAP[message.level.upper()]
        msg = message.data.get("msg")
        extra = message.data.get("extra")
        logger.log(level, msg, extra=extra)

    async with Client(config, log_handler=log_handler) as client:
        result = await client.call_tool("test_server_log_test", {"message": "test 42"})
        assert result.data == 42
        assert len(MESSAGES) == 1
        assert MESSAGES[0].data["msg"] == "test 42"

        # Filter to only our test logger (exclude OpenTelemetry internal logs)
        test_records = [r for r in caplog.records if r.name == __name__]
        assert len(test_records) == 1
        assert test_records[0].msg == "test 42"


async def test_multi_client_with_transforms(tmp_path: Path):
    """
    Tests that transforms are properly applied to the tools.
    """
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
                "tools": {
                    "add": {
                        "name": "transformed_add",
                        "arguments": {
                            "a": {"name": "transformed_a"},
                            "b": {"name": "transformed_b"},
                        },
                    }
                },
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    client = Client[MCPConfigTransport](config)

    async with client:
        tools = await client.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        assert len(tools) == 2
        assert "test_1_transformed_add" in tools_by_name

        result = await client.call_tool(
            "test_1_transformed_add", {"transformed_a": 1, "transformed_b": 2}
        )
        assert result.data == 3


async def test_canonical_multi_client_with_transforms(tmp_path: Path):
    """Test that transforms are not applied to servers in a canonical MCPConfig."""
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = CanonicalMCPConfig(
        mcpServers={
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
                "tools": {  # <--- Will be ignored as it's not valid for a canonical MCPConfig
                    "add": {
                        "name": "transformed_add",
                        "arguments": {
                            "a": {"name": "transformed_a"},
                            "b": {"name": "transformed_b"},
                        },
                    }
                },
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }  # type: ignore[reportUnknownArgumentType]
    )

    client = Client(config)

    async with client:
        tools = await client.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        assert len(tools) == 2
        assert "test_1_transformed_add" not in tools_by_name


@pytest.mark.flaky(retries=3)
async def test_multi_client_transform_with_filtering(tmp_path: Path):
    """
    Tests that tag-based filtering works when using a transforming MCPConfig.
    """
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        @mcp.tool
        def subtract(a: int, b: int) -> int:
            return a - b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
                "tools": {
                    "add": {
                        "name": "transformed_add",
                        "tags": ["keep"],
                        "arguments": {
                            "a": {"name": "transformed_a"},
                            "b": {"name": "transformed_b"},
                        },
                    },
                },
                "include_tags": ["keep"],
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    client = Client[MCPConfigTransport](config)

    async with client:
        tools = await client.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        assert len(tools) == 3
        assert "test_1_transformed_add" in tools_by_name
        assert "test_1_add" not in tools_by_name
        assert "test_1_subtract" not in tools_by_name
        assert "test_2_add" in tools_by_name
        assert "test_2_subtract" in tools_by_name


async def test_multi_client_with_elicitation(tmp_path: Path):
    """
    Tests that elicitation is properly forwarded to the ultimate client.
    """
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP, Context

        mcp = FastMCP()

        @mcp.tool
        async def elicit_test(ctx: Context) -> int:
            result = await ctx.elicit('Pick a number', response_type=int)
            return result.data

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_server": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_server_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    async def elicitation_handler(message, response_type, params, ctx):
        return response_type(value=42)

    async with Client(config, elicitation_handler=elicitation_handler) as client:
        result = await client.call_tool("test_server_elicit_test", {})
        assert result.data == 42


async def test_multi_server_config_transport(tmp_path: Path):
    """
    Tests that MCPConfigTransport properly handles multi-server configurations.

    Related to https://github.com/jlowin/fastmcp/issues/2802 - verifies the
    refactored architecture creates composite servers correctly.
    """
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "greet_server.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "server1": {
                "command": "python",
                "args": [str(script_path)],
            },
            "server2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    # Create client with multiple servers
    client = Client(config)
    assert isinstance(client.transport, MCPConfigTransport)

    # Verify both servers are accessible via prefixed tool names
    async with client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "server1_greet" in tool_names
        assert "server2_greet" in tool_names

        # Call tools on both servers
        result1 = await client.call_tool("server1_greet", {"name": "World"})
        assert isinstance(result1.content[0], TextContent)
        assert "Hello, World!" in result1.content[0].text

        result2 = await client.call_tool("server2_greet", {"name": "FastMCP"})
        assert isinstance(result2.content[0], TextContent)
        assert "Hello, FastMCP!" in result2.content[0].text


async def test_multi_server_timeout_propagation():
    """Test that timeout is correctly propagated to proxy clients in multi-server configs."""
    # Create a config with multiple servers
    config = MCPConfig(
        mcpServers={
            "server1": StdioMCPServer(command="echo", args=["test"]),
            "server2": StdioMCPServer(command="echo", args=["test"]),
        }
    )

    transport = MCPConfigTransport(config)
    timeout = timedelta(seconds=42)

    # Patch _create_proxy to verify timeout is passed correctly
    with (
        patch("fastmcp.client.transports.FastMCP.as_proxy") as mock_as_proxy,
        patch.object(
            transport, "_create_proxy", wraps=transport._create_proxy
        ) as mock_create_proxy,
    ):
        # Make as_proxy return a mock FastMCP
        mock_proxy = FastMCP(name="MockProxy")
        mock_as_proxy.return_value = mock_proxy

        # Mock connect_session on FastMCPTransport to avoid actual connection
        with patch(
            "fastmcp.client.transports.FastMCPTransport.connect_session"
        ) as mock_connect:
            mock_session = AsyncMock()
            mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

            async with transport.connect_session(read_timeout_seconds=timeout):
                pass

        # Verify _create_proxy was called with the timeout for each server
        assert mock_create_proxy.call_count == 2
        for call in mock_create_proxy.call_args_list:
            _, kwargs = call.args, call.kwargs if call.kwargs else {}
            # Third positional arg is timeout
            call_timeout = call[0][2] if len(call[0]) > 2 else kwargs.get("timeout")
            assert call_timeout == timeout, (
                f"Expected timeout {timeout}, got {call_timeout}"
            )


async def test_single_server_config_transport():
    """Test that single-server configs delegate directly without creating a composite."""
    config = MCPConfig(
        mcpServers={
            "only_server": StdioMCPServer(command="echo", args=["test"]),
        }
    )

    transport = MCPConfigTransport(config)

    # Single server should have transport created eagerly (not at connect time)
    assert hasattr(transport, "transport")
    assert isinstance(transport.transport, StdioTransport)

    # _transports should already contain the single transport
    assert len(transport._transports) == 1


def sample_tool_fn(arg1: int, arg2: str) -> str:
    return f"Hello, world! {arg1} {arg2}"


@pytest.fixture
def sample_tool() -> FastMCPTool:
    return FastMCPTool.from_function(sample_tool_fn, name="sample_tool")


@pytest.fixture
async def test_script(tmp_path: Path) -> AsyncGenerator[Path, Any]:
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def fetch(url: str) -> str:

            return f"Hello, world! {url}"

        if __name__ == '__main__':
            mcp.run()
        """)

        yield Path(f.name)

    pass
