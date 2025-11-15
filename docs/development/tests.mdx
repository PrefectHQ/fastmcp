---
title: "Tests"
description: "Testing patterns and requirements for FastMCP"
icon: vial
---

import { VersionBadge } from "/snippets/version-badge.mdx"

Good tests are the foundation of reliable software. In FastMCP, we treat tests as first-class documentation that demonstrates how features work while protecting against regressions. Every new capability needs comprehensive tests that demonstrate correctness.

## FastMCP Tests

### Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/server/test_auth.py

# Run with coverage
uv run pytest --cov=fastmcp

# Skip integration tests for faster runs
uv run pytest -m "not integration"

# Skip tests that spawn processes
uv run pytest -m "not integration and not client_process"
```

Tests should complete in under 1 second unless marked as integration tests. This speed encourages running them frequently, catching issues early.

### Test Organization

Our test organization mirrors the `src/` directory structure, creating a predictable mapping between code and tests. When you're working on `src/fastmcp/server/auth.py`, you'll find its tests in `tests/server/test_auth.py`. In rare cases tests are split further - for example, the OpenAPI tests are so comprehensive they're split across multiple files.

### Test Markers

We use pytest markers to categorize tests that require special resources or take longer to run:

```python
@pytest.mark.integration
async def test_github_api_integration():
    """Test GitHub API integration with real service."""
    token = os.getenv("FASTMCP_GITHUB_TOKEN")
    if not token:
        pytest.skip("FASTMCP_GITHUB_TOKEN not available")
    
    # Test against real GitHub API
    client = GitHubClient(token)
    repos = await client.list_repos("jlowin")
    assert "fastmcp" in [repo.name for repo in repos]

@pytest.mark.client_process
async def test_stdio_transport():
    """Test STDIO transport with separate process."""
    # This spawns a subprocess
    async with Client("python examples/simple_echo.py") as client:
        result = await client.call_tool("echo", {"message": "test"})
        assert result.content[0].text == "test"
```

## Writing Tests


### Test Requirements

Following these practices creates maintainable, debuggable test suites that serve as both documentation and regression protection.

#### Single Behavior Per Test

Each test should verify exactly one behavior. When it fails, you need to know immediately what broke. A test that checks five things gives you five potential failure points to investigate. A test that checks one thing points directly to the problem.

<CodeGroup>

```python Good: Atomic Test
async def test_tool_registration():
    """Test that tools are properly registered with the server."""
    mcp = FastMCP("test-server")
    
    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b
    
    tools = mcp.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "add"
```

```python Bad: Multi-Behavior Test
async def test_server_functionality():
    """Test multiple server features at once."""
    mcp = FastMCP("test-server")
    
    # Tool registration
    @mcp.tool
    def add(a: int, b: int) -> int:
        return a + b
    
    # Resource creation
    @mcp.resource("config://app")
    def get_config():
        return {"version": "1.0"}
    
    # Authentication setup
    mcp.auth = BearerTokenProvider({"token": "user"})
    
    # What exactly are we testing? If this fails, what broke?
    assert mcp.list_tools()
    assert mcp.list_resources()
    assert mcp.auth is not None
```

</CodeGroup>

#### Self-Contained Setup

Every test must create its own setup. Tests should be runnable in any order, in parallel, or in isolation. When a test fails, you should be able to run just that test to reproduce the issue.

<CodeGroup>

```python Good: Self-Contained
async def test_tool_execution_with_error():
    """Test that tool errors are properly handled."""
    mcp = FastMCP("test-server")
    
    @mcp.tool
    def divide(a: int, b: int) -> float:
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
    
    async with Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("divide", {"a": 10, "b": 0})
```

```python Bad: Test Dependencies
# Global state that tests depend on
test_server = None

def test_setup_server():
    """Setup for other tests."""
    global test_server
    test_server = FastMCP("shared-server")

def test_server_works():
    """Test server functionality."""
    # Depends on test_setup_server running first
    assert test_server is not None
```

</CodeGroup>

#### Clear Intent

Test names and assertions should make the verified behavior obvious. A developer reading your test should understand what feature it validates and how that feature should behave.

```python
async def test_authenticated_tool_requires_valid_token():
    """Test that authenticated users can access protected tools."""
    mcp = FastMCP("test-server")
    mcp.auth = BearerTokenProvider({"secret-token": "test-user"})
    
    @mcp.tool
    def protected_action() -> str:
        return "success"
    
    async with Client(mcp, auth=BearerAuth("secret-token")) as client:
        result = await client.call_tool("protected_action", {})
        assert result.content[0].text == "success"
```

#### Using Fixtures

Use fixtures to create reusable data, server configurations, or other resources for your tests. Note that you should **not** open FastMCP clients in your fixtures as it can create hard-to-diagnose issues with event loops.

```python
import pytest
from fastmcp import FastMCP, Client

@pytest.fixture
def weather_server():
    server = FastMCP("WeatherServer")
    
    @server.tool
    def get_temperature(city: str) -> dict:
        temps = {"NYC": 72, "LA": 85, "Chicago": 68}
        return {"city": city, "temp": temps.get(city, 70)}
    
    return server

async def test_temperature_tool(weather_server):
    async with Client(weather_server) as client:
        result = await client.call_tool("get_temperature", {"city": "LA"})
        assert result.data == {"city": "LA", "temp": 85}
```

#### Effective Assertions

Assertions should be specific and provide context on failure. When a test fails during CI, the assertion message should tell you exactly what went wrong.

```python
# Basic assertion - minimal context on failure
assert result.status == "success"

# Better - explains what was expected
assert result.status == "success", f"Expected successful operation, got {result.status}: {result.error}"
```

Try not to have too many assertions in a single test unless you truly need to check various aspects of the same behavior. In general, assertions of different behaviors should be in separate tests.

#### Inline Snapshots

FastMCP uses `inline-snapshot` for testing complex data structures. On first run of `pytest --inline-snapshot=create` with an empty `snapshot()`, pytest will auto-populate the expected value. To update snapshots after intentional changes, run `pytest --inline-snapshot=fix`. This is particularly useful for testing JSON schemas and API responses.

```python
from inline_snapshot import snapshot

async def test_tool_schema_generation():
    """Test that tool schemas are generated correctly."""
    mcp = FastMCP("test-server")
    
    @mcp.tool
    def calculate_tax(amount: float, rate: float = 0.1) -> dict:
        """Calculate tax on an amount."""
        return {"amount": amount, "tax": amount * rate, "total": amount * (1 + rate)}
    
    tools = mcp.list_tools()
    schema = tools[0].inputSchema
    
    # First run: snapshot() is empty, gets auto-populated
    # Subsequent runs: compares against stored snapshot
    assert schema == snapshot({
        "type": "object", 
        "properties": {
            "amount": {"type": "number"}, 
            "rate": {"type": "number", "default": 0.1}
        }, 
        "required": ["amount"]
    })
```

### In-Memory Testing

FastMCP uses in-memory transport for testing, where servers and clients communicate directly. The majority of functionality can be tested in a deterministic fashion this way. We use more complex setups only when testing transports themselves.

The in-memory transport runs the real MCP protocol implementation without network overhead. Instead of deploying your server or managing network connections, you pass your server instance directly to the client. Everything runs in the same Python process - you can set breakpoints anywhere and step through with your debugger.

```python
from fastmcp import FastMCP, Client

# Create your server
server = FastMCP("WeatherServer")

@server.tool
def get_temperature(city: str) -> dict:
    """Get current temperature for a city"""
    temps = {"NYC": 72, "LA": 85, "Chicago": 68}
    return {"city": city, "temp": temps.get(city, 70)}

async def test_weather_operations():
    # Pass server directly - no deployment needed
    async with Client(server) as client:
        result = await client.call_tool("get_temperature", {"city": "NYC"})
        assert result.data == {"city": "NYC", "temp": 72}
```

This pattern makes tests deterministic and fast - typically completing in milliseconds rather than seconds.

### Mocking External Dependencies

FastMCP servers are standard Python objects, so you can mock external dependencies using your preferred approach:

```python
from unittest.mock import AsyncMock

async def test_database_tool():
    server = FastMCP("DataServer")
    
    # Mock the database
    mock_db = AsyncMock()
    mock_db.fetch_users.return_value = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"}
    ]
    
    @server.tool
    async def list_users() -> list:
        return await mock_db.fetch_users()
    
    async with Client(server) as client:
        result = await client.call_tool("list_users", {})
        assert len(result.data) == 2
        assert result.data[0]["name"] == "Alice"
        mock_db.fetch_users.assert_called_once()
```

### Testing Network Transports

While in-memory testing covers most unit testing needs, you'll occasionally need to test actual network transports like HTTP or SSE. FastMCP provides two approaches: in-process async servers (preferred), and separate subprocess servers (for special cases).

#### In-Process Network Testing (Preferred)

<VersionBadge version="2.13.0" />

For most network transport tests, use `run_server_async` as an async context manager. This runs the server as a task in the same process, providing fast, deterministic tests with full debugger support:

```python
import pytest
from fastmcp import FastMCP, Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.tests import run_server_async

def create_test_server() -> FastMCP:
    """Create a test server instance."""
    server = FastMCP("TestServer")

    @server.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    return server

@pytest.fixture
async def http_server() -> str:
    """Start server in-process for testing."""
    server = create_test_server()
    async with run_server_async(server) as url:
        yield url

async def test_http_transport(http_server: str):
    """Test actual HTTP transport behavior."""
    async with Client(
        transport=StreamableHttpTransport(http_server)
    ) as client:
        result = await client.ping()
        assert result is True

        greeting = await client.call_tool("greet", {"name": "World"})
        assert greeting.data == "Hello, World!"
```

The `run_server_async` context manager automatically handles server lifecycle and cleanup. This approach is faster than subprocess-based testing and provides better error messages.

#### Subprocess Testing (Special Cases)

For tests that require complete process isolation (like STDIO transport or testing subprocess behavior), use `run_server_in_process`:

```python
import pytest
from fastmcp.utilities.tests import run_server_in_process
from fastmcp import FastMCP, Client
from fastmcp.client.transports import StreamableHttpTransport

def run_server(host: str, port: int) -> None:
    """Function to run in subprocess."""
    server = FastMCP("TestServer")
    
    @server.tool
    def greet(name: str) -> str:
        return f"Hello, {name}!"
    
    server.run(host=host, port=port)

@pytest.fixture
async def http_server():
    """Fixture that runs server in subprocess."""
    with run_server_in_process(run_server, transport="http") as url:
        yield f"{url}/mcp"

async def test_http_transport(http_server: str):
    """Test actual HTTP transport behavior."""
    async with Client(
        transport=StreamableHttpTransport(http_server)
    ) as client:
        result = await client.ping()
        assert result is True
```

The `run_server_in_process` utility handles server lifecycle, port allocation, and cleanup automatically. Use this only when subprocess isolation is truly necessary, as it's slower and harder to debug than in-process testing. FastMCP uses the `client_process` marker to isolate these tests in CI.

### Documentation Testing

Documentation requires the same validation as code. The `just docs` command launches a local Mintlify server that renders your documentation exactly as users will see it:

```bash
# Start local documentation server with hot reload
just docs

# Or run Mintlify directly
mintlify dev
```

The local server watches for changes and automatically refreshes. This preview catches formatting issues and helps you see documentation as users will experience it.