import asyncio
import gc
import inspect
import logging
import os
import sys
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import psutil
import pytest

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
    running_under_debugger() or sys.platform.startswith("win32"),
    reason="Debugger holds a reference to the transport; Windows has process lifecycle issues",
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


@pytest.mark.skipif(
    sys.platform.startswith("win32"),
    reason="Windows has process lifecycle issues",
)
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

        assert len(caplog.records) == 1
        assert caplog.records[0].msg == "test 42"


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
