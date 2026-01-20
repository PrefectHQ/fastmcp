<div align="center">

<!-- omit in toc -->

<picture>
  <source width="550" media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/f-watercolor-waves-dark-2.png">
  <source width="550" media="(prefers-color-scheme: light)" srcset="docs/assets/brand/f-watercolor-waves-2.png">
  <img width="550" alt="FastMCP Logo" src="docs/assets/brand/f-watercolor-waves-2.png">
</picture>

# FastMCP v2 🚀

<strong>The fast, Pythonic way to build MCP servers and clients.</strong>

*Made with ☕️ by [Prefect](https://www.prefect.io/)*

[![Docs](https://img.shields.io/badge/docs-gofastmcp.com-blue)](https://gofastmcp.com)
[![Discord](https://img.shields.io/badge/community-discord-5865F2?logo=discord&logoColor=white)](https://discord.gg/uu8dJCgttd)
[![PyPI - Version](https://img.shields.io/pypi/v/fastmcp.svg)](https://pypi.org/project/fastmcp)
[![Tests](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml/badge.svg)](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml)
[![License](https://img.shields.io/github/license/jlowin/fastmcp.svg)](https://github.com/jlowin/fastmcp/blob/main/LICENSE)

<a href="https://trendshift.io/repositories/13266" target="_blank"><img src="https://trendshift.io/api/badge/repositories/13266" alt="jlowin%2Ffastmcp | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</div>

> [!Note]
>
> #### FastMCP 2.0: The Standard Framework
>
> FastMCP pioneered Python MCP development, and FastMCP 1.0 was incorporated into the [official MCP SDK](https://github.com/modelcontextprotocol/python-sdk) in 2024.
>
> **This is FastMCP 2.0** — the actively maintained, production-ready framework that extends far beyond basic protocol implementation. While the SDK provides core functionality, FastMCP 2.0 delivers everything needed for production: advanced MCP patterns (server composition, proxying, OpenAPI/FastAPI generation, tool transformation), enterprise auth (Google, GitHub, WorkOS, Azure, Auth0, and more), deployment tools, testing utilities, and comprehensive client libraries.
>
> **For production MCP applications, install FastMCP:** `pip install fastmcp`

> [!Important]
> FastMCP 3.0 is in development and may include some breaking changes. To avoid unexpected issues, pin your dependency to v2: `fastmcp<3`

---

**FastMCP is the standard framework for building MCP applications**, providing the fastest path from idea to production.

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io) is a standardized way to provide context and tools to LLMs. FastMCP makes building production-ready MCP servers simple, with enterprise auth, deployment tools, and a complete ecosystem built in.

```python
# server.py
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

if __name__ == "__main__":
    mcp.run()
```

Run the server locally:

```bash
fastmcp run server.py
```

### 📚 Documentation

FastMCP's complete documentation is available at **[gofastmcp.com](https://gofastmcp.com)**, including detailed guides, API references, and advanced patterns. This readme provides only a high-level overview.

Documentation is also available in [llms.txt format](https://llmstxt.org/), which is a simple markdown standard that LLMs can consume easily.

There are two ways to access the LLM-friendly documentation:

- [`llms.txt`](https://gofastmcp.com/llms.txt) is essentially a sitemap, listing all the pages in the documentation.
- [`llms-full.txt`](https://gofastmcp.com/llms-full.txt) contains the entire documentation. Note this may exceed the context window of your LLM.

**Community:** Join our [Discord server](https://discord.gg/uu8dJCgttd) to connect with other FastMCP developers and share what you're building.

---

<!-- omit in toc -->
## Table of Contents

- [FastMCP v2 🚀](#fastmcp-v2-)
  - [📚 Documentation](#-documentation)
  - [What is MCP?](#what-is-mcp)
  - [Why FastMCP?](#why-fastmcp)
  - [Installation](#installation)
  - [Core Concepts](#core-concepts)
    - [The `FastMCP` Server](#the-fastmcp-server)
    - [Tools](#tools)
    - [Resources \& Templates](#resources--templates)
    - [Prompts](#prompts)
    - [Context](#context)
    - [MCP Clients](#mcp-clients)
  - [Authentication](#authentication)
    - [Enterprise Authentication, Zero Configuration](#enterprise-authentication-zero-configuration)
  - [Deployment](#deployment)
    - [From Development to Production](#from-development-to-production)
  - [Advanced Features](#advanced-features)
    - [Proxy Servers](#proxy-servers)
    - [Composing MCP Servers](#composing-mcp-servers)
    - [OpenAPI \& FastAPI Generation](#openapi--fastapi-generation)
  - [Running Your Server](#running-your-server)
  - [Contributing](#contributing)
    - [Prerequisites](#prerequisites)
    - [Setup](#setup)
    - [Unit Tests](#unit-tests)
    - [Static Checks](#static-checks)
    - [Pull Requests](#pull-requests)

---

## What is MCP?

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io) lets you build servers that expose data and functionality to LLM applications in a secure, standardized way. It is often described as "the USB-C port for AI", providing a uniform way to connect LLMs to resources they can use. It may be easier to think of it as an API, but specifically designed for LLM interactions. MCP servers can:

- Expose data through **Resources** (think of these sort of like GET endpoints; they are used to load information into the LLM's context)
- Provide functionality through **Tools** (sort of like POST endpoints; they are used to execute code or otherwise produce a side effect)
- Define interaction patterns through **Prompts** (reusable templates for LLM interactions)
- And more!

FastMCP provides a high-level, Pythonic interface for building, managing, and interacting with these servers.

## Why FastMCP?

FastMCP handles all the complex protocol details so you can focus on building. In most cases, decorating a Python function is all you need — FastMCP handles the rest.

🚀 **Fast:** High-level interface means less code and faster development

🍀 **Simple:** Build MCP servers with minimal boilerplate

🐍 **Pythonic:** Feels natural to Python developers

🔍 **Complete:** Everything for production — enterprise auth (Google, GitHub, Azure, Auth0, WorkOS), deployment tools, testing frameworks, client libraries, and more

FastMCP provides the shortest path from idea to production. Deploy locally, to the cloud with [FastMCP Cloud](https://fastmcp.cloud), or to your own infrastructure.

## Installation

We recommend installing FastMCP with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install fastmcp
```

For full installation instructions, including verification, upgrading from the official MCPSDK, and developer setup, see the [**Installation Guide**](https://gofastmcp.com/getting-started/installation).

**Dependency Licensing:** FastMCP depends on Cyclopts for CLI functionality. Cyclopts v4 includes docutils as a transitive dependency, which has complex licensing that may trigger compliance reviews in some organizations. If this is a concern, you can install Cyclopts v5 alpha (`pip install "cyclopts>=5.0.0a1"`) which removes this dependency, or wait for the stable v5 release. See [this issue](https://github.com/BrianPugh/cyclopts/issues/672) for details.

## Core Concepts

These are the building blocks for creating MCP servers and clients with FastMCP.

### The `FastMCP` Server

The central object representing your MCP application. It holds your tools, resources, and prompts, manages connections, and can be configured with settings like authentication.

```python
from fastmcp import FastMCP

# Create a server instance
mcp = FastMCP(name="MyAssistantServer")
```

Learn more in the [**FastMCP Server Documentation**](https://gofastmcp.com/servers/server).

### Tools

Tools allow LLMs to perform actions by executing your Python functions (sync or async). Ideal for computations, API calls, or side effects (like `POST`/`PUT`). FastMCP handles schema generation from type hints and docstrings. Tools can return various types, including text, JSON-serializable objects, and even images or audio aided by the FastMCP media helper classes.

```python
@mcp.tool
def multiply(a: float, b: float) -> float:
    """Multiplies two numbers."""
    return a * b
```

Learn more in the [**Tools Documentation**](https://gofastmcp.com/servers/tools).

### Resources & Templates

Resources expose read-only data sources (like `GET` requests). Use `@mcp.resource("your://uri")`. Use `{placeholders}` in the URI to create dynamic templates that accept parameters, allowing clients to request specific data subsets.

```python
# Static resource
@mcp.resource("config://version")
def get_version(): 
    return "2.0.1"

# Dynamic resource template
@mcp.resource("users://{user_id}/profile")
def get_profile(user_id: int):
    # Fetch profile for user_id...
    return {"name": f"User {user_id}", "status": "active"}
```

Learn more in the [**Resources & Templates Documentation**](https://gofastmcp.com/servers/resources).

### Prompts

Prompts define reusable message templates to guide LLM interactions. Decorate functions with `@mcp.prompt`. Return strings or `Message` objects.

```python
@mcp.prompt
def summarize_request(text: str) -> str:
    """Generate a prompt asking for a summary."""
    return f"Please summarize the following text:\n\n{text}"
```

Learn more in the [**Prompts Documentation**](https://gofastmcp.com/servers/prompts).

### Context

Access MCP session capabilities within your tools, resources, or prompts by adding a `ctx: Context` parameter. Context provides methods for:

- **Logging:** Log messages to MCP clients with `ctx.info()`, `ctx.error()`, etc.
- **LLM Sampling:** Use `ctx.sample()` to request completions from the client's LLM.
- **Resource Access:** Use `ctx.read_resource()` to access resources on the server
- **Progress Reporting:** Use `ctx.report_progress()` to report progress to the client.
- and more...

To access the context, add a parameter annotated as `Context` to any mcp-decorated function. FastMCP will automatically inject the correct context object when the function is called.

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("My MCP Server")

@mcp.tool
async def process_data(uri: str, ctx: Context):
    # Log a message to the client
    await ctx.info(f"Processing {uri}...")

    # Read a resource from the server
    data = await ctx.read_resource(uri)

    # Ask client LLM to summarize the data
    summary = await ctx.sample(f"Summarize: {data.content[:500]}")

    # Return the summary
    return summary.text
```

Learn more in the [**Context Documentation**](https://gofastmcp.com/servers/context).

### MCP Clients

Interact with *any* MCP server programmatically using the `fastmcp.Client`. It supports various transports (Stdio, SSE, In-Memory) and often auto-detects the correct one. The client can also handle advanced patterns like server-initiated **LLM sampling requests** if you provide an appropriate handler.

Critically, the client allows for efficient **in-memory testing** of your servers by connecting directly to a `FastMCP` server instance via the `FastMCPTransport`, eliminating the need for process management or network calls during tests.

```python
from fastmcp import Client

async def main():
    # Connect via stdio to a local script
    async with Client("my_server.py") as client:
        tools = await client.list_tools()
        print(f"Available tools: {tools}")
        result = await client.call_tool("add", {"a": 5, "b": 3})
        print(f"Result: {result.content[0].text}")

    # Connect via SSE
    async with Client("http://localhost:8000/sse") as client:
        # ... use the client
        pass
```

To use clients to test servers, use the following pattern:

```python
from fastmcp import FastMCP, Client

mcp = FastMCP("My MCP Server")

async def main():
    # Connect via in-memory transport
    async with Client(mcp) as client:
        # ... use the client
```

FastMCP also supports connecting to multiple servers through a single unified client using the standard MCP configuration format:

```python
from fastmcp import Client

# Standard MCP configuration with multiple servers
config = {
    "mcpServers": {
        "weather": {"url": "https://weather-api.example.com/mcp"},
        "assistant": {"command": "python", "args": ["./assistant_server.py"]}
    }
}

# Create a client that connects to all servers
client = Client(config)

async def main():
    async with client:
        # Access tools and resources with server prefixes
        forecast = await client.call_tool("weather_get_forecast", {"city": "London"})
        answer = await client.call_tool("assistant_answer_question", {"query": "What is MCP?"})
```

Learn more in the [**Client Documentation**](https://gofastmcp.com/clients/client) and [**Transports Documentation**](https://gofastmcp.com/clients/transports).

## Authentication

### Enterprise Authentication, Zero Configuration

FastMCP provides comprehensive authentication support that sets it apart from basic MCP implementations. Secure your servers and authenticate your clients with the same enterprise-grade providers used by major corporations.

**Built-in OAuth Providers:**

- **Google**
- **GitHub**
- **Microsoft Azure**
- **Auth0**
- **WorkOS**
- **Descope**
- **Discord**
- **JWT/Custom**
- **API Keys**

Protecting a server takes just two lines:

```python
from fastmcp.server.auth.providers.google import GoogleProvider

auth = GoogleProvider(client_id="...", client_secret="...", base_url="https://myserver.com")
mcp = FastMCP("Protected Server", auth=auth)
```

Connecting to protected servers is even simpler:

```python
async with Client("https://protected-server.com/mcp", auth="oauth") as client:
    # Automatic browser-based OAuth flow
    result = await client.call_tool("protected_tool")
```

**Why FastMCP Auth Matters:**

- **Production-Ready:** Persistent storage, token refresh, comprehensive error handling
- **Zero-Config OAuth:** Just pass `auth="oauth"` for automatic setup
- **Enterprise Integration:** WorkOS SSO, Azure Active Directory, Auth0 tenants
- **Developer Experience:** Automatic browser launch, local callback server, environment variable support
- **Advanced Architecture:** Full OIDC support, Dynamic Client Registration (DCR), and unique OAuth proxy pattern that enables DCR with any provider

*Authentication this comprehensive is unique to FastMCP 2.0.*

Learn more in the **Authentication Documentation** for [servers](https://gofastmcp.com/servers/auth) and [clients](https://gofastmcp.com/clients/auth).

## Deployment

### From Development to Production

FastMCP supports every deployment scenario from local development to global scale:

**Development:** Run locally with a single command

```bash
fastmcp run server.py
```

**Production:** Deploy to [**FastMCP Cloud**](https://fastmcp.cloud) — Remote MCP that just works

- Instant HTTPS endpoints
- Built-in authentication
- Zero configuration
- Free for personal servers

**Self-Hosted:** Use HTTP or SSE transports for your own infrastructure

```python
mcp.run(transport="http", host="0.0.0.0", port=8000)
```

Learn more in the [**Deployment Documentation**](https://gofastmcp.com/deployment).

## Advanced Features

FastMCP introduces powerful ways to structure and compose your MCP applications.

### Proxy Servers

Create a FastMCP server that acts as an intermediary for another local or remote MCP server using `FastMCP.as_proxy()`. This is especially useful for bridging transports (e.g., remote SSE to local Stdio) or adding a layer of logic to a server you don't control.

Learn more in the [**Proxying Documentation**](https://gofastmcp.com/patterns/proxy).

### Composing MCP Servers

Build modular applications by mounting multiple `FastMCP` instances onto a parent server using `mcp.mount()` (live link) or `mcp.import_server()` (static copy).

Learn more in the [**Composition Documentation**](https://gofastmcp.com/patterns/composition).

### OpenAPI & FastAPI Generation

Automatically generate FastMCP servers from existing OpenAPI specifications (`FastMCP.from_openapi()`) or FastAPI applications (`FastMCP.from_fastapi()`), instantly bringing your web APIs to the MCP ecosystem.

Learn more: [**OpenAPI Integration**](https://gofastmcp.com/integrations/openapi) | [**FastAPI Integration**](https://gofastmcp.com/integrations/fastapi).

## Running Your Server

The main way to run a FastMCP server is by calling the `run()` method on your server instance:

```python
# server.py
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool
def hello(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()  # Default: uses STDIO transport
```

FastMCP supports three transport protocols:

**STDIO (Default)**: Best for local tools and command-line scripts.

```python
mcp.run(transport="stdio")  # Default, so transport argument is optional
```

**Streamable HTTP**: Recommended for web deployments.

```python
mcp.run(transport="http", host="127.0.0.1", port=8000, path="/mcp")
```

**SSE**: For compatibility with existing SSE clients.

```python
mcp.run(transport="sse", host="127.0.0.1", port=8000)
```

See the [**Running Server Documentation**](https://gofastmcp.com/deployment/running-server) for more details.

## Contributing

Contributions are the core of open source! We welcome improvements and features.

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (Recommended for environment management)

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/jlowin/fastmcp.git 
   cd fastmcp
   ```

2. Create and sync the environment:

   ```bash
   uv sync
   ```

   This installs all dependencies, including dev tools.

3. Activate the virtual environment (e.g., `source .venv/bin/activate` or via your IDE).

### Unit Tests

FastMCP has a comprehensive unit test suite. All PRs must introduce or update tests as appropriate and pass the full suite.

Run tests using pytest:

```bash
pytest
```

or if you want an overview of the code coverage

```bash
uv run pytest --cov=src --cov=examples --cov-report=html
```

### Static Checks

FastMCP uses `prek` for code formatting, linting, and type-checking. All PRs must pass these checks (they run automatically in CI).

Install the hooks locally:

```bash
uv run prek install
```

The hooks will now run automatically on `git commit`. You can also run them manually at any time:

```bash
prek run --all-files
# or via uv
uv run prek run --all-files
```

### Pull Requests

1. Fork the repository on GitHub.
2. Create a feature branch from `main`.
3. Make your changes, including tests and documentation updates.
4. Ensure tests and prek hooks pass.
5. Commit your changes and push to your fork.
6. Open a pull request against the `main` branch of `jlowin/fastmcp`.

Please open an issue or discussion for questions or suggestions before starting significant work!
