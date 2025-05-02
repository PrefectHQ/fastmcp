<div align="center">

<!-- omit in toc -->
# FastMCP v2 🚀
<strong>The fast, Pythonic way to build MCP servers and clients.</strong>

[![Docs](https://img.shields.io/badge/docs-gofastmcp.com-blue)](https://gofastmcp.com)
[![PyPI - Version](https://img.shields.io/pypi/v/fastmcp.svg)](https://pypi.org/project/fastmcp)
[![Tests](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml/badge.svg)](https://github.com/jlowin/fastmcp/actions/workflows/run-tests.yml)
[![License](https://img.shields.io/github/license/jlowin/fastmcp.svg)](https://github.com/jlowin/fastmcp/blob/main/LICENSE)

<a href="https://trendshift.io/repositories/13266" target="_blank"><img src="https://trendshift.io/api/badge/repositories/13266" alt="jlowin%2Ffastmcp | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</div>

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io) is a new, standardized way to provide context and tools to your LLMs, and FastMCP makes building MCP servers and clients simple and intuitive. Create tools, expose resources, define prompts, and connect components with clean, Pythonic code.

```python
# server.py
from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")

@mcp.tool()
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

FastMCP handles the complex protocol details and server management, letting you focus on building great tools and applications. It's designed to feel natural to Python developers.


<!-- omit in toc -->
## Table of Contents

- [What is MCP?](#what-is-mcp)
- [Why FastMCP?](#why-fastmcp)
- [Key Features](#key-features)
  - [Servers](#servers)
  - [Clients](#clients)
- [What's New in v2?](#whats-new-in-v2)
- [Documentation](#documentation)
  - [Installation](#installation)
  - [Quickstart](#quickstart)
- [Core Concepts](#core-concepts)
  - [The `FastMCP` Server](#the-fastmcp-server)
  - [Tools](#tools)
  - [Resources](#resources)
  - [Prompts](#prompts)
  - [Context](#context)
  - [Images](#images)
  - [MCP Clients](#mcp-clients)
    - [Client Methods](#client-methods)
    - [Transport Options](#transport-options)
    - [LLM Sampling](#llm-sampling)
    - [Roots Access](#roots-access)
- [Advanced Features](#advanced-features)
  - [Proxy Servers](#proxy-servers)
  - [Composing MCP Servers](#composing-mcp-servers)
  - [OpenAPI \& FastAPI Generation](#openapi--fastapi-generation)
  - [Handling `stderr`](#handling-stderr)
- [Running Your Server](#running-your-server)
  - [Development Mode (Recommended for Building \& Testing)](#development-mode-recommended-for-building--testing)
  - [Claude Desktop Integration (For Regular Use)](#claude-desktop-integration-for-regular-use)
  - [Direct Execution (For Advanced Use Cases)](#direct-execution-for-advanced-use-cases)
  - [Server Object Names](#server-object-names)
- [Examples](#examples)
- [Contributing](#contributing)
    - [Prerequisites](#prerequisites)
    - [Setup](#setup)
    - [Testing](#testing)
    - [Formatting \& Linting](#formatting--linting)
    - [Pull Requests](#pull-requests)


## What is MCP?

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io) lets you build servers that expose data and functionality to LLM applications in a secure, standardized way. Think of it like a web API, but specifically designed for LLM interactions. MCP servers can:

- Expose data through **Resources** (think GET endpoints; load info into context)
- Provide functionality through **Tools** (think POST/PUT endpoints; execute actions)
- Define interaction patterns through **Prompts** (reusable templates)
- And more!

FastMCP provides a high-level, Pythonic interface for building and interacting with these servers.

## Why FastMCP?

The MCP protocol is powerful but implementing it involves a lot of boilerplate - server setup, protocol handlers, content types, error management. FastMCP handles all the complex protocol details and server management, so you can focus on building great tools. It's designed to be high-level and Pythonic; in most cases, decorating a function is all you need.

FastMCP aims to be:


🚀 **Fast:** High-level interface means less code and faster development

🍀 **Simple:** Build MCP servers with minimal boilerplate

🐍 **Pythonic:** Feels natural to Python developers

🔍 **Complete:** FastMCP aims to provide a full implementation of the core MCP specification for both servers and clients

## Key Features

### Servers
- **Create** servers with minimal boilerplate using intuitive decorators
- **Proxy** existing servers to modify configuration or transport
- **Compose**  servers into complex applications
- **Generate** servers from OpenAPI specs or FastAPI objects

### Clients
- **Interact** with MCP servers programmatically
- **Connect** to any MCP server using any transport
- **Test** your servers without manual intervention
- **Innovate** with core MCP capabilities like LLM sampling


## What's New in v2?

FastMCP 1.0 made it so easy to build MCP servers that it's now part of the [official Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk)! For basic use cases, you can use the upstream version by importing `mcp.server.fastmcp.FastMCP` (or installing `fastmcp=1.0`). 

Based on how the MCP ecosystem is evolving, FastMCP 2.0 builds on that foundation to introduce a variety of new features (and more experimental ideas). It adds advanced features like proxying and composing MCP servers, as well as automatically generating them from OpenAPI specs or FastAPI objects. FastMCP 2.0 also introduces new client-side functionality like LLM sampling.


## Documentation

📚 FastMCP's documentation is available at [gofastmcp.com](https://gofastmcp.com).

---

### Installation

We strongly recommend installing FastMCP with [uv](https://docs.astral.sh/uv/), as it is required for deploying servers via the CLI:

```bash
uv pip install fastmcp
```

Note: on macOS, uv may need to be installed with Homebrew (`brew install uv`) in order to make it available to the Claude Desktop app.

For development, install with:
```bash
# Clone the repo first
git clone https://github.com/jlowin/fastmcp.git
cd fastmcp
# Install with dev dependencies
uv sync
```

### Quickstart

Let's create a simple MCP server that exposes a calculator tool and some data:

```python
# server.py
from fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("Demo")

# Add an addition tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"
```

You can install this server in [Claude Desktop](https://claude.ai/download) and interact with it right away by running:
```bash
fastmcp install server.py
```

![MCP Inspector](/docs/assets/demo-inspector.png)


## Core Concepts

These are the building blocks for creating MCP servers, using the familiar decorator-based approach.

### The `FastMCP` Server

The central object representing your MCP application. It handles connections, protocol details, and routing.

```python
from fastmcp import FastMCP

# Create a named server
mcp = FastMCP("My App")

# Specify dependencies needed when deployed via `fastmcp install`
mcp = FastMCP("My App", dependencies=["pandas", "numpy"])
```

### Tools

Tools allow LLMs to perform actions by executing your Python functions. They are ideal for tasks that involve computation, external API calls, or side effects.

Decorate synchronous or asynchronous functions with `@mcp.tool()`. FastMCP automatically generates the necessary MCP schema based on type hints and docstrings. Pydantic models can be used for complex inputs.

```python
import httpx
from pydantic import BaseModel

class UserInfo(BaseModel):
    user_id: int
    notify: bool = False

@mcp.tool()
async def send_notification(user: UserInfo, message: str) -> dict:
    """Sends a notification to a user if requested."""
    if user.notify:
        # Simulate sending notification
        print(f"Notifying user {user.user_id}: {message}")
        return {"status": "sent", "user_id": user.user_id}
    return {"status": "skipped", "user_id": user.user_id}

@mcp.tool()
def get_stock_price(ticker: str) -> float:
    """Gets the current price for a stock ticker."""
    # Replace with actual API call
    prices = {"AAPL": 180.50, "GOOG": 140.20}
    return prices.get(ticker.upper(), 0.0)
```

### Resources

Resources expose data to LLMs. They should primarily provide information without significant computation or side effects (like GET requests).

Decorate functions with `@mcp.resource("your://uri")`. Use curly braces `{}` in the URI to define dynamic resources (templates) where parts of the URI become function parameters.

```python
# Static resource returning simple text
@mcp.resource("config://app-version")
def get_app_version() -> str:
    """Returns the application version."""
    return "v2.1.0"

# Dynamic resource template expecting a 'user_id' from the URI
@mcp.resource("db://users/{user_id}/email")
async def get_user_email(user_id: str) -> str:
    """Retrieves the email address for a given user ID."""
    # Replace with actual database lookup
    emails = {"123": "alice@example.com", "456": "bob@example.com"}
    return emails.get(user_id, "not_found@example.com")

# Resource returning JSON data
@mcp.resource("data://product-categories")
def get_categories() -> list[str]:
    """Returns a list of available product categories."""
    return ["Electronics", "Books", "Home Goods"]
```

### Prompts

Prompts define reusable templates or interaction patterns for the LLM. They help guide the LLM on how to use your server's capabilities effectively.

Decorate functions with `@mcp.prompt()`. The function should return the desired prompt content, which can be a simple string, a `Message` object (like `UserMessage` or `AssistantMessage`), or a list of these.

```python
from fastmcp.prompts.base import UserMessage, AssistantMessage

@mcp.prompt()
def ask_review(code_snippet: str) -> str:
    """Generates a standard code review request."""
    return f"Please review the following code snippet for potential bugs and style issues:\n```python\n{code_snippet}\n```"

@mcp.prompt()
def debug_session_start(error_message: str) -> list[Message]:
    """Initiates a debugging help session."""
    return [
        UserMessage(f"I encountered an error:\n{error_message}"),
        AssistantMessage("Okay, I can help with that. Can you provide the full traceback and tell me what you were trying to do?")
    ]
```

### Context

Gain access to MCP server capabilities *within* your tool or resource functions by adding a parameter type-hinted with `fastmcp.Context`.

```python
from fastmcp import Context, FastMCP

mcp = FastMCP("Context Demo")

@mcp.resource("system://status")
async def get_system_status(ctx: Context) -> dict:
    """Checks system status and logs information."""
    await ctx.info("Checking system status...")
    # Perform checks
    await ctx.report_progress(1, 1) # Report completion
    return {"status": "OK", "load": 0.5, "client": ctx.client_id}

@mcp.tool()
async def process_large_file(file_uri: str, ctx: Context) -> str:
    """Processes a large file, reporting progress and reading resources."""
    await ctx.info(f"Starting processing for {file_uri}")
    # Read the resource using the context
    file_content_resource = await ctx.read_resource(file_uri)
    file_content = file_content_resource[0].content # Assuming single text content
    lines = file_content.splitlines()
    total_lines = len(lines)

    for i, line in enumerate(lines):
        # Process line...
        if (i + 1) % 100 == 0: # Report progress every 100 lines
            await ctx.report_progress(i + 1, total_lines)

    await ctx.info(f"Finished processing {file_uri}")
    return f"Processed {total_lines} lines."

```

The `Context` object provides:
*   Logging: `ctx.debug()`, `ctx.info()`, `ctx.warning()`, `ctx.error()`
*   Progress Reporting: `ctx.report_progress(current, total)`
*   Resource Access: `await ctx.read_resource(uri)`
*   Request Info: `ctx.request_id`, `ctx.client_id`
*   Sampling (Advanced): `await ctx.sample(...)` to ask the connected LLM client for completions.

### Images

Easily handle image outputs using the `fastmcp.Image` helper class.

<Tip>
The below code requires the `pillow` library to be installed.
</Tip>

```python
from mcp.server.fastmcp import FastMCP, Image
from io import BytesIO
try:
    from PIL import Image as PILImage
except ImportError:
    raise ImportError("Please install the `pillow` library to run this example.")

mcp = FastMCP("My App")

@mcp.tool()
def create_thumbnail(image_path: str) -> Image:
    """Create a thumbnail from an image"""
    img = PILImage.open(image_path)
    img.thumbnail((100, 100))    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return Image(data=buffer.getvalue(), format="png")
```
Return the `Image` helper class from your tool to send an image to the client. The `Image` helper class handles the conversion to/from the base64-encoded format required by the MCP protocol. It works with either a path to an image file, or a bytes object.


### MCP Clients

The `Client` class lets you interact with any MCP server (not just FastMCP ones) from Python code:

```python
from fastmcp import Client

async with Client("path/to/server") as client:
    # Call a tool
    result = await client.call_tool("weather", {"location": "San Francisco"})
    print(result)
    
    # Read a resource
    res = await client.read_resource("db://users/123/profile")
    print(res)
```

You can connect to servers using any supported transport protocol (Stdio, SSE, FastMCP, etc.). If you don't specify a transport, the `Client` class automatically attempts to detect an appropriate one from your connection string or server object.

#### Client Methods

The `Client` class exposes several methods for interacting with MCP servers.

```python
async with Client("path/to/server") as client:
    # List available tools
    tools = await client.list_tools()
    
    # List available resources
    resources = await client.list_resources()
    
    # Call a tool with arguments
    result = await client.call_tool("generate_report", {"user_id": 123})
    
    # Read a resource
    user_data = await client.read_resource("db://users/123/profile")
        
    # Get a prompt
    greeting = await client.get_prompt("welcome", {"name": "Alice"})
    
    # Send progress updates
    await client.progress("task-123", 50, 100)  # 50% complete
    
    # Basic connectivity testing
    await client.ping()
```

These methods correspond directly to MCP protocol operations, making it easy to interact with any MCP-compatible server (not just FastMCP ones).

#### Transport Options

FastMCP supports various transport protocols for connecting to MCP servers:

```python
from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport, 
    PythonStdioTransport, 
    FastMCPTransport
)

# Connect to a server over SSE (common for web-based MCP servers)
async with Client(SSETransport("http://localhost:8000/mcp")) as client:
    # Use client here...

# Connect to a Python script using stdio (useful for local tools)
async with Client(PythonStdioTransport("path/to/script.py")) as client:
    # Use client here...

# Connect directly to a FastMCP server object in the same process
from your_app import mcp_server
async with Client(FastMCPTransport(mcp_server)) as client:
    # Use client here...
```

Common transport options include:
- `SSETransport`: Connect to a server via Server-Sent Events (HTTP)
- `PythonStdioTransport`: Run a Python script and communicate via stdio
- `FastMCPTransport`: Connect directly to a FastMCP server object
- `WSTransport`: Connect via WebSockets

In addition, if you pass a connection string or `FastMCP` server object to the `Client` constructor, it will try to automatically detect the appropriate transport.

#### LLM Sampling

Sampling is an MCP feature that allows a server to request a completion from the client LLM, enabling sophisticated use cases while maintaining security and privacy on the server.

```python
import marvin  # Or any other LLM client
from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams

# -- SERVER SIDE --
# Create a server that requests LLM completions from the client

mcp = FastMCP("Sampling Example")

@mcp.tool()
async def generate_poem(topic: str, context: Context) -> str:
    """Generate a short poem about the given topic."""
    # The server requests a completion from the client LLM
    response = await context.sample(
        f"Write a short poem about {topic}",
        system_prompt="You are a talented poet who writes concise, evocative verses."
    )
    return response.text

@mcp.tool()
async def summarize_document(document_uri: str, context: Context) -> str:
    """Summarize a document using client-side LLM capabilities."""
    # First read the document as a resource
    doc_resource = await context.read_resource(document_uri)
    doc_content = doc_resource[0].content  # Assuming single text content
    
    # Then ask the client LLM to summarize it
    response = await context.sample(
        f"Summarize the following document:\n\n{doc_content}",
        system_prompt="You are an expert summarizer. Create a concise summary."
    )
    return response.text

# -- CLIENT SIDE --
# Create a client that handles the sampling requests

async def sampling_handler(
    messages: list[SamplingMessage],
    params: SamplingParams,
    ctx: RequestContext,
) -> str:
    """Handle sampling requests from the server using your preferred LLM."""
    # Extract the messages and system prompt
    prompt = [m.content.text for m in messages if m.content.type == "text"]
    system_instruction = params.systemPrompt or "You are a helpful assistant."
    
    # Use your preferred LLM client to generate completions
    return await marvin.say_async(
        message=prompt,
        instructions=system_instruction,
    )

# Connect them together
async with Client(mcp, sampling_handler=sampling_handler) as client:
    result = await client.call_tool("generate_poem", {"topic": "autumn leaves"})
    print(result.content[0].text)
```

This pattern is powerful because:
1. The server can delegate text generation to the client LLM
2. The server remains focused on business logic and data handling
3. The client maintains control over which LLM is used and how requests are handled
4. No sensitive data needs to be sent to external APIs 

#### Roots Access

FastMCP exposes the MCP roots functionality, allowing clients to specify which file system roots they can access. This creates a secure boundary for tools that need to work with files. Note that the server must account for client roots explicitly.

```python
from fastmcp import Client, RootsList

# Specify file roots that the client can access
roots = ["file:///path/to/allowed/directory"]

async with Client(mcp_server, roots=roots) as client:
    # Now tools in the MCP server can access files in the specified roots
    await client.call_tool("process_file", {"filename": "data.csv"})
```

## Advanced Features

Building on the core concepts, FastMCP v2 introduces powerful features for more complex scenarios:


### Proxy Servers

Create a FastMCP server that acts as an intermediary, proxying requests to another MCP endpoint (which could be a server or another client connection).

**Use Cases:**

*   **Transport Conversion:** Expose a server running on Stdio (like many local tools) over SSE or WebSockets, making it accessible to web clients or Claude Desktop.
*   **Adding Functionality:** Wrap an existing server to add authentication, request logging, or modified tool behavior.
*   **Aggregating Servers:** Combine multiple backend MCP servers behind a single proxy interface (though `mount` might be simpler for this).

```python
import asyncio
from fastmcp import FastMCP, Client
from fastmcp.client.transports import PythonStdioTransport

# Create a client that connects to the original server
proxy_client = Client(
    transport=PythonStdioTransport('path/to/original_stdio_server.py'),
)

# Create a proxy server that connects to the client and exposes its capabilities
proxy = FastMCP.from_client(proxy_client, name="Stdio-to-SSE Proxy")

if __name__ == "__main__":
    proxy.run(transport='sse')
```

`FastMCP.from_client` is a class method that connects to the target, discovers its capabilities, and dynamically builds the proxy server instance.



### Composing MCP Servers

Structure larger MCP applications by creating modular FastMCP servers and "mounting" them onto a parent server. This automatically handles prefixing for tool names and resource URIs, preventing conflicts.

```python
from fastmcp import FastMCP

# --- Weather MCP ---
weather_mcp = FastMCP("Weather Service")

@weather_mcp.tool()
def get_forecast(city: str): 
    return f"Sunny in {city}"

@weather_mcp.resource("data://temp/{city}")
def get_temp(city: str): 
    return 25.0

# --- News MCP ---
news_mcp = FastMCP("News Service")

@news_mcp.tool()
def fetch_headlines():
    return ["Big news!", "Other news"]

@news_mcp.resource("data://latest_story")
def get_story():
    return "A story happened."

# --- Composite MCP ---

mcp = FastMCP("Composite")

# Mount sub-apps with prefixes
mcp.mount("weather", weather_mcp) # Tools prefixed "weather/", resources prefixed "weather+"
mcp.mount("news", news_mcp)       # Tools prefixed "news/", resources prefixed "news+"

@mcp.tool()
def ping(): 
    return "Composite OK"


if __name__ == "__main__":
    mcp.run()
```

This promotes code organization and reusability for complex MCP systems.

### OpenAPI & FastAPI Generation

Leverage your existing web APIs by automatically generating FastMCP servers from them.

By default, the following rules are applied:
- `GET` requests -> MCP resources
- `GET` requests with path parameters -> MCP resource templates
- All other HTTP methods -> MCP tools
  
You can override these rules to customize or even ignore certain endpoints.

**From FastAPI:**

```python
from fastapi import FastAPI
from fastmcp import FastMCP

# Your existing FastAPI application
fastapi_app = FastAPI(title="My Existing API")

@fastapi_app.get("/status")
def get_status(): 
    return {"status": "running"}

@fastapi_app.post("/items")
def create_item(name: str, price: float): 
    return {"id": 1, "name": name, "price": price}

# Generate an MCP server directly from the FastAPI app
mcp_server = FastMCP.from_fastapi(fastapi_app)

if __name__ == "__main__":
    mcp_server.run()
```

**From an OpenAPI Specification:**

```python
import httpx
import json
from fastmcp import FastMCP

# Load the OpenAPI spec (dict)
# with open("my_api_spec.json", "r") as f:
#     openapi_spec = json.load(f)
openapi_spec = { ... } # Your spec dict

# Create an HTTP client to make requests to the actual API endpoint
http_client = httpx.AsyncClient(base_url="https://api.yourservice.com")

# Generate the MCP server
mcp_server = FastMCP.from_openapi(openapi_spec, client=http_client)

if __name__ == "__main__":
    mcp_server.run()
```

### Handling `stderr`
The MCP spec allows for the server to write anything it wants to `stderr`, and it
doesn't specify the format in any way. FastMCP will forward the server's `stderr`
to the client's `stderr`.

## Running Your Server

Choose the method that best suits your needs:

### Development Mode (Recommended for Building & Testing)

Use `fastmcp dev` for an interactive testing environment with the MCP Inspector.

```bash
fastmcp dev your_server_file.py
# With temporary dependencies
fastmcp dev your_server_file.py --with pandas --with numpy
# With local package in editable mode
fastmcp dev your_server_file.py --with-editable .
```

### Claude Desktop Integration (For Regular Use)

Use `fastmcp install` to set up your server for persistent use within the Claude Desktop app. It handles creating an isolated environment using `uv`.

```bash
fastmcp install your_server_file.py
# With a custom name in Claude
fastmcp install your_server_file.py --name "My Analysis Tool"
# With extra packages and environment variables
fastmcp install server.py --with requests -v API_KEY=123 -f .env
```

### Direct Execution (For Advanced Use Cases)

Run your server script directly for custom deployments or integrations outside of Claude. You manage the environment and dependencies yourself.

Add to your `your_server_file.py`:
```python
if __name__ == "__main__":
    mcp.run() # Assuming 'mcp' is your FastMCP instance
```
Run with:
```bash
python your_server_file.py
# or
uv run python your_server_file.py
```

### Server Object Names

If your `FastMCP` instance is not named `mcp`, `server`, or `app`, specify it using `file:object` syntax for the `dev` and `install` commands:

```bash
fastmcp dev my_module.py:my_mcp_instance
fastmcp install api.py:api_app
```

## Examples

Explore the `examples/` directory for code samples demonstrating various features:

*   `simple_echo.py`: Basic tool, resource, and prompt.
*   `complex_inputs.py`: Using Pydantic models for tool inputs.
*   `mount_example.py`: Mounting multiple FastMCP servers.
*   `sampling.py`: Using LLM completions within your MCP server.
*   `screenshot.py`: Tool returning an Image object.
*   `text_me.py`: Tool interacting with an external API.
*   `memory.py`: More complex example with database interaction.

## Contributing

Contributions make the open-source community vibrant! We welcome improvements and features.

<details>

<summary><h3>Open Developer Guide</h3></summary>

#### Prerequisites

*   Python 3.10+
*   [uv](https://docs.astral.sh/uv/)

#### Setup

1.  Clone: `git clone https://github.com/jlowin/fastmcp.git && cd fastmcp`
2.  Install Env & Dependencies: `uv venv && uv sync` (Activate the `.venv` after creation)

#### Testing

Run the test suite:
```bash
uv run --frozen pytest -vv
```

#### Formatting & Linting

We use `ruff` via `pre-commit`.
1.  Install hooks: `pre-commit install`
2.  Run checks: `pre-commit run --all-files`

#### Pull Requests

1.  Fork the repository.
2.  Create a feature branch.
3.  Make changes, commit, and push to your fork.
4.  Open a pull request against the `main` branch of `jlowin/fastmcp`.

Please open an issue or discussion for questions or suggestions!

</details>
