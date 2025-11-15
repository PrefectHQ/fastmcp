# FastMCP Testing Demo

A comprehensive example demonstrating FastMCP testing patterns with pytest-asyncio.

## Overview

This example shows how to:
- Set up pytest-asyncio configuration in `pyproject.toml`
- Write async test fixtures for MCP clients
- Test tools, resources, and prompts
- Use parametrized tests for multiple scenarios
- Leverage inline-snapshot and dirty-equals for assertions

## Project Structure

```
testing_demo/
├── pyproject.toml          # Project config with pytest-asyncio setup
├── server.py               # Simple MCP server with tools/resources/prompts
├── tests/
│   └── test_server.py      # Comprehensive test suite
└── README.md               # This file
```

## Key Features

### pyproject.toml Configuration

The `pyproject.toml` includes the critical pytest-asyncio configuration:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

This eliminates the need for `@pytest.mark.asyncio` decorators on every async test.

### Server Components

The demo server (`server.py`) includes:
- **Tools**: `add`, `greet`, `async_multiply`
- **Resources**: `demo://info`, `demo://greeting/{name}`
- **Prompts**: `hello`, `explain`

### Test Patterns

The test suite demonstrates:
1. **Async fixture pattern**: Proper client fixture using `async with`
2. **Tool testing**: Calling tools and checking results via `.data` attribute
3. **Resource testing**: Reading static and templated resources
4. **Prompt testing**: Getting prompts with different arguments
5. **Parametrized tests**: Testing multiple scenarios efficiently
6. **Schema validation**: Verifying tool schemas and structure
7. **Pattern matching**: Using dirty-equals for flexible assertions

## Running the Tests

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run a specific test
uv run pytest tests/test_server.py::test_add_tool
```

## Running the Server

```bash
# Run the server
uv run fastmcp run server.py

# Inspect the server
uv run fastmcp inspect server.py
```

## Learning More

For detailed information about testing FastMCP servers, see the [Testing Documentation](../../docs/patterns/testing.mdx).
