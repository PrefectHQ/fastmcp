---
title: Client Transports
sidebarTitle: Transports
description: Configure how FastMCP Clients connect to and communicate with servers.
icon: link
---

import { VersionBadge } from "/snippets/version-badge.mdx"

<VersionBadge version="2.0.0" />

The FastMCP `Client` communicates with MCP servers through transport objects that handle the underlying connection mechanics. While the client can automatically select a transport based on what you pass to it, instantiating transports explicitly gives you full control over configuration—environment variables, authentication, session management, and more.

Think of transports as configurable adapters between your client code and MCP servers. Each transport type handles a different communication pattern: subprocesses with pipes, HTTP connections, or direct in-memory calls.

## Choosing the Right Transport

- **Use [STDIO Transport](#stdio-transport)** when you need to run local MCP servers with full control over their environment and lifecycle
- **Use [Remote Transports](#remote-transports)** when connecting to production services or shared MCP servers running independently  
- **Use [In-Memory Transport](#in-memory-transport)** for testing FastMCP servers without subprocess or network overhead
- **Use [MCP JSON Configuration](#mcp-json-configuration-transport)** when you need to connect to multiple servers defined in configuration files

## STDIO Transport

STDIO (Standard Input/Output) transport communicates with MCP servers through subprocess pipes. This is the standard mechanism used by desktop clients like Claude Desktop and is the primary way to run local MCP servers.

### The Client Runs the Server

<Warning>
**Critical Concept**: When using STDIO transport, your client actually launches and manages the server process. This is fundamentally different from network transports where you connect to an already-running server. Understanding this relationship is key to using STDIO effectively.
</Warning>

With STDIO transport, your client:
- Starts the server as a subprocess when you connect
- Manages the server's lifecycle (start, stop, restart)
- Controls the server's environment and configuration
- Communicates through stdin/stdout pipes

This architecture enables powerful local integrations but requires understanding environment isolation and process management.

### Environment Isolation

STDIO servers run in isolated environments by default. This is a security feature enforced by the MCP protocol to prevent accidental exposure of sensitive data.

When your client launches an MCP server:
- The server does NOT inherit your shell's environment variables
- API keys, paths, and other configuration must be explicitly passed
- The working directory and system paths may differ from your shell

To pass environment variables to your server, use the `env` parameter:

```python
from fastmcp import Client

# If your server needs environment variables (like API keys),
# you must explicitly pass them:
client = Client(
    "my_server.py",
    env={"API_KEY": "secret", "DEBUG": "true"}
)

# This won't work - the server runs in isolation:
# export API_KEY="secret"  # in your shell
# client = Client("my_server.py")  # server can't see API_KEY
```

### Basic Usage

To use STDIO transport, you create a transport instance with the command and arguments needed to run your server:

```python
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(
    command="python",
    args=["my_server.py"]
)
client = Client(transport)
```

You can configure additional settings like environment variables, working directory, or command arguments:

```python
transport = StdioTransport(
    command="python",
    args=["my_server.py", "--verbose"],
    env={"LOG_LEVEL": "DEBUG"},
    cwd="/path/to/server"
)
client = Client(transport)
```

For convenience, the client can also infer STDIO transport from file paths, but this doesn't allow configuration:

```python
from fastmcp import Client

client = Client("my_server.py")  # Limited - no configuration options
```

### Environment Variables

Since STDIO servers don't inherit your environment, you need strategies for passing configuration. Here are two common approaches:

**Selective forwarding** passes only the variables your server actually needs:

```python
import os
from fastmcp.client.transports import StdioTransport

required_vars = ["API_KEY", "DATABASE_URL", "REDIS_HOST"]
env = {
    var: os.environ[var] 
    for var in required_vars 
    if var in os.environ
}

transport = StdioTransport(
    command="python",
    args=["server.py"],
    env=env
)
client = Client(transport)
```

**Loading from .env files** keeps configuration separate from code:

```python
from dotenv import dotenv_values
from fastmcp.client.transports import StdioTransport

env = dotenv_values(".env")
transport = StdioTransport(
    command="python",
    args=["server.py"],
    env=env
)
client = Client(transport)
```

### Session Persistence

STDIO transports maintain sessions across multiple client contexts by default (`keep_alive=True`). This improves performance by reusing the same subprocess for multiple connections, but can be controlled when you need isolation.

By default, the subprocess persists between connections:

```python
from fastmcp.client.transports import StdioTransport

transport = StdioTransport(
    command="python",
    args=["server.py"]
)
client = Client(transport)

async def efficient_multiple_operations():
    async with client:
        await client.ping()
    
    async with client:  # Reuses the same subprocess
        await client.call_tool("process_data", {"file": "data.csv"})
```

For complete isolation between connections, disable session persistence:

```python
transport = StdioTransport(
    command="python",
    args=["server.py"],
    keep_alive=False
)
client = Client(transport)
```

Use `keep_alive=False` when you need complete isolation (e.g., in test suites) or when server state could cause issues between connections.

### Specialized STDIO Transports

FastMCP provides convenience transports that are thin wrappers around `StdioTransport` with pre-configured commands:

- **`PythonStdioTransport`** - Uses `python` command for `.py` files
- **`NodeStdioTransport`** - Uses `node` command for `.js` files  
- **`UvStdioTransport`** - Uses `uv` for Python packages (uses `env_vars` parameter)
- **`UvxStdioTransport`** - Uses `uvx` for Python packages (uses `env_vars` parameter)
- **`NpxStdioTransport`** - Uses `npx` for Node packages (uses `env_vars` parameter)

For most use cases, instantiate `StdioTransport` directly with your desired command. These specialized transports are primarily useful for client inference shortcuts.

## Remote Transports

Remote transports connect to MCP servers running as web services. This is a fundamentally different model from STDIO transports—instead of your client launching and managing a server process, you connect to an already-running service that manages its own environment and lifecycle.

### Streamable HTTP Transport

<VersionBadge version="2.3.0" />

Streamable HTTP is the recommended transport for production deployments, providing efficient bidirectional streaming over HTTP connections.

- **Class:** `StreamableHttpTransport`
- **Server compatibility:** FastMCP servers running with `mcp run --transport http`

The transport requires a URL and optionally supports custom headers for authentication and configuration:

```python
from fastmcp.client.transports import StreamableHttpTransport

# Basic connection
transport = StreamableHttpTransport(url="https://api.example.com/mcp")
client = Client(transport)

# With custom headers for authentication
transport = StreamableHttpTransport(
    url="https://api.example.com/mcp",
    headers={
        "Authorization": "Bearer your-token-here",
        "X-Custom-Header": "value"
    }
)
client = Client(transport)
```

For convenience, FastMCP also provides authentication helpers:

```python
from fastmcp.client.auth import BearerAuth

client = Client(
    "https://api.example.com/mcp",
    auth=BearerAuth("your-token-here")
)
```

### SSE Transport (Legacy)

Server-Sent Events transport is maintained for backward compatibility but is superseded by Streamable HTTP for new deployments.

- **Class:** `SSETransport`  
- **Server compatibility:** FastMCP servers running with `mcp run --transport sse`

SSE transport supports the same configuration options as Streamable HTTP:

```python
from fastmcp.client.transports import SSETransport

transport = SSETransport(
    url="https://api.example.com/sse",
    headers={"Authorization": "Bearer token"}
)
client = Client(transport)
```

Use Streamable HTTP for new deployments unless you have specific infrastructure requirements for SSE.

## In-Memory Transport

In-memory transport connects directly to a FastMCP server instance within the same Python process. This eliminates both subprocess management and network overhead, making it ideal for testing and development.

- **Class:** `FastMCPTransport`

<Note>
Unlike STDIO transports, in-memory servers have full access to your Python process's environment. They share the same memory space and environment variables as your client code—no isolation or explicit environment passing required.
</Note>

```python
from fastmcp import FastMCP, Client
import os

mcp = FastMCP("TestServer")

@mcp.tool
def greet(name: str) -> str:
    prefix = os.environ.get("GREETING_PREFIX", "Hello")
    return f"{prefix}, {name}!"

client = Client(mcp)

async with client:
    result = await client.call_tool("greet", {"name": "World"})
```

## MCP JSON Configuration Transport

<VersionBadge version="2.4.0" />

This transport supports the emerging MCP JSON configuration standard for defining multiple servers:

- **Class:** `MCPConfigTransport`

```python
config = {
    "mcpServers": {
        "weather": {
            "url": "https://weather.example.com/mcp",
            "transport": "http"
        },
        "assistant": {
            "command": "python",
            "args": ["./assistant.py"],
            "env": {"LOG_LEVEL": "INFO"}
        }
    }
}

client = Client(config)

async with client:
    # Tools are namespaced by server
    weather = await client.call_tool("weather_get_forecast", {"city": "NYC"})
    answer = await client.call_tool("assistant_ask", {"question": "What?"})
```

### Tool Transformation with FastMCP and MCPConfig

FastMCP supports basic tool transformations to be defined alongside the MCP Servers in the MCPConfig file.

```python
config = {
    "mcpServers": {
        "weather": {
            "url": "https://weather.example.com/mcp",
            "transport": "http",
            "tools": { }   #  <--- This is the tool transformation section
        }
    }
}
```

With these transformations, you can transform (change) the name, title, description, tags, enablement, and arguments of a tool.

For each argument the tool takes, you can transform (change) the name, description, default, visibility, whether it's required, and you can provide example values.

In the following example, we're transforming the `weather_get_forecast` tool to only retrieve the weather for `Miami` and hiding the `city` argument from the client.

```python
tool_transformations = {
    "weather_get_forecast": {
        "name": "miami_weather",
        "description": "Get the weather for Miami",
        "arguments": {
            "city": {
                "name": "city",
                "default": "Miami",
                "hide": True,
            }
        }
    }
}

config = {
    "mcpServers": {
        "weather": {
            "url": "https://weather.example.com/mcp",
            "transport": "http",
            "tools": tool_transformations
        }
    }
}
```

#### Allowlisting and Blocklisting Tools

Tools can be allowlisted or blocklisted from the client by applying `tags` to the tools on the server. In the following example, we're allowlisting only tools marked with the `forecast` tag, all other tools will be unavailable to the client.

```python
tool_transformations = {
    "weather_get_forecast": {
        "enabled": True,
        "tags": ["forecast"]
    }
}


config = {
    "mcpServers": {
        "weather": {
            "url": "https://weather.example.com/mcp",
            "transport": "http",
            "tools": tool_transformations,
            "include_tags": ["forecast"]
        }
    }
}
```