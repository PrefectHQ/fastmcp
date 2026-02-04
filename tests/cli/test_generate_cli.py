"""Tests for fastmcp generate-cli command."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import mcp.types
import pytest

from fastmcp import FastMCP
from fastmcp.cli import generate as generate_module
from fastmcp.cli.client import Client
from fastmcp.cli.generate import (
    _derive_server_name,
    _schema_to_python_type,
    _to_python_identifier,
    _tool_function_source,
    generate_cli_command,
    generate_cli_script,
    serialize_transport,
)
from fastmcp.client.transports.stdio import StdioTransport

# ---------------------------------------------------------------------------
# _schema_to_python_type
# ---------------------------------------------------------------------------


class TestSchemaToPythonType:
    def test_simple_string(self):
        py_type, needs_json = _schema_to_python_type({"type": "string"})
        assert py_type == "str"
        assert needs_json is False

    def test_simple_integer(self):
        py_type, needs_json = _schema_to_python_type({"type": "integer"})
        assert py_type == "int"
        assert needs_json is False

    def test_simple_number(self):
        py_type, needs_json = _schema_to_python_type({"type": "number"})
        assert py_type == "float"
        assert needs_json is False

    def test_simple_boolean(self):
        py_type, needs_json = _schema_to_python_type({"type": "boolean"})
        assert py_type == "bool"
        assert needs_json is False

    def test_array_of_strings(self):
        py_type, needs_json = _schema_to_python_type(
            {"type": "array", "items": {"type": "string"}}
        )
        assert py_type == "list[str]"
        assert needs_json is False

    def test_array_of_integers(self):
        py_type, needs_json = _schema_to_python_type(
            {"type": "array", "items": {"type": "integer"}}
        )
        assert py_type == "list[int]"
        assert needs_json is False

    def test_complex_object(self):
        py_type, needs_json = _schema_to_python_type({"type": "object"})
        assert py_type == "str"
        assert needs_json is True

    def test_complex_nested_array(self):
        py_type, needs_json = _schema_to_python_type(
            {"type": "array", "items": {"type": "object"}}
        )
        assert py_type == "str"
        assert needs_json is True

    def test_union_of_simple_types(self):
        py_type, needs_json = _schema_to_python_type({"type": ["string", "null"]})
        assert py_type == "str | None"
        assert needs_json is False


# ---------------------------------------------------------------------------
# _to_python_identifier
# ---------------------------------------------------------------------------


class TestToPythonIdentifier:
    def test_plain_name(self):
        assert _to_python_identifier("hello") == "hello"

    def test_hyphens(self):
        assert _to_python_identifier("get-forecast") == "get_forecast"

    def test_dots_and_slashes(self):
        assert _to_python_identifier("a.b/c") == "a_b_c"

    def test_leading_digit(self):
        assert _to_python_identifier("3d_render") == "_3d_render"

    def test_spaces(self):
        assert _to_python_identifier("my tool") == "my_tool"

    def test_empty_string(self):
        assert _to_python_identifier("") == "_unnamed"


# ---------------------------------------------------------------------------
# serialize_transport
# ---------------------------------------------------------------------------


class TestSerializeTransport:
    def test_url_string(self):
        code, imports = serialize_transport("http://localhost:8000/mcp")
        assert code == "'http://localhost:8000/mcp'"
        assert imports == set()

    def test_stdio_transport_basic(self):
        transport = StdioTransport(command="fastmcp", args=["run", "server.py"])
        code, imports = serialize_transport(transport)
        assert "StdioTransport" in code
        assert "command='fastmcp'" in code
        assert "args=['run', 'server.py']" in code
        assert "from fastmcp.client.transports import StdioTransport" in imports

    def test_stdio_transport_with_env(self):
        transport = StdioTransport(
            command="python", args=["-m", "myserver"], env={"KEY": "val"}
        )
        code, imports = serialize_transport(transport)
        assert "env={'KEY': 'val'}" in code

    def test_dict_passthrough(self):
        d: dict[str, Any] = {"mcpServers": {"test": {"url": "http://localhost"}}}
        code, imports = serialize_transport(d)
        assert "mcpServers" in code
        assert imports == set()


# ---------------------------------------------------------------------------
# _tool_function_source
# ---------------------------------------------------------------------------


class TestToolFunctionSource:
    def test_required_param(self):
        tool = mcp.types.Tool(
            name="greet",
            inputSchema={
                "properties": {"name": {"type": "string", "description": "Who"}},
                "required": ["name"],
            },
        )
        source = _tool_function_source(tool)
        assert "async def greet(" in source
        assert "name: Annotated[str" in source
        assert "= None" not in source
        assert "_call_tool('greet', {'name': name})" in source

    def test_optional_param(self):
        tool = mcp.types.Tool(
            name="search",
            inputSchema={
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
                "required": ["query"],
            },
        )
        source = _tool_function_source(tool)
        assert "query: Annotated[str" in source
        assert "limit: Annotated[int | None" in source
        assert "= None" in source

    def test_param_with_default(self):
        tool = mcp.types.Tool(
            name="fetch",
            inputSchema={
                "properties": {
                    "url": {"type": "string", "description": "URL"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout",
                        "default": 30,
                    },
                },
                "required": ["url"],
            },
        )
        source = _tool_function_source(tool)
        assert "timeout: Annotated[int" in source
        assert "= 30" in source

    def test_no_params(self):
        tool = mcp.types.Tool(
            name="ping",
            inputSchema={"properties": {}},
        )
        source = _tool_function_source(tool)
        assert "async def ping(" in source
        assert "_call_tool('ping', {})" in source

    def test_preserves_underscores(self):
        tool = mcp.types.Tool(
            name="get_forecast",
            inputSchema={
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        source = _tool_function_source(tool)
        assert "async def get_forecast(" in source

    def test_sanitizes_tool_name(self):
        tool = mcp.types.Tool(
            name="my.tool/v2",
            inputSchema={"properties": {}},
        )
        source = _tool_function_source(tool)
        assert "async def my_tool_v2(" in source
        assert "name='my.tool/v2'" in source

    def test_sanitizes_param_name(self):
        tool = mcp.types.Tool(
            name="fetch",
            inputSchema={
                "properties": {"content-type": {"type": "string", "description": "CT"}},
                "required": ["content-type"],
            },
        )
        source = _tool_function_source(tool)
        assert "content_type: Annotated[str" in source
        assert "'content-type': content_type" in source

    def test_description_in_docstring(self):
        tool = mcp.types.Tool(
            name="greet",
            description="Say hello to someone.",
            inputSchema={
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
        source = _tool_function_source(tool)
        assert "'''Say hello to someone.'''" in source

    def test_description_with_quotes(self):
        tool = mcp.types.Tool(
            name="fetch",
            description="Fetch data from 'source' API.",
            inputSchema={
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        )
        source = _tool_function_source(tool)
        # Should escape single quotes in the description
        assert r"Fetch data from \'source\' API." in source
        # Generated code should compile
        compile(source, "<test>", "exec")

    def test_array_of_strings_parameter(self):
        tool = mcp.types.Tool(
            name="tag_items",
            description="Tag multiple items.",
            inputSchema={
                "properties": {
                    "item_id": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item_id"],
            },
        )
        source = _tool_function_source(tool)
        # Should use list[str] type with help metadata
        assert "tags: Annotated[list[str]" in source
        assert "= []" in source
        # Should not have JSON parsing for simple arrays
        assert "json.loads" not in source
        compile(source, "<test>", "exec")

    def test_complex_object_parameter(self):
        tool = mcp.types.Tool(
            name="create_user",
            description="Create a user.",
            inputSchema={
                "properties": {
                    "name": {"type": "string"},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "dept": {"type": "string"},
                        },
                    },
                },
                "required": ["name"],
            },
        )
        source = _tool_function_source(tool)
        # Should use str type for complex object
        assert "metadata: Annotated[str | None" in source
        # Should include JSON schema in help (with escaped quotes)
        assert "JSON Schema:" in source
        assert '\\"type\\": \\"object\\"' in source
        # Should have JSON parsing with isinstance check
        assert (
            "metadata_parsed = json.loads(metadata) if isinstance(metadata, str) else metadata"
            in source
        )
        # Should use parsed version in call
        assert "'metadata': metadata_parsed" in source
        compile(source, "<test>", "exec")

    def test_nested_array_parameter(self):
        tool = mcp.types.Tool(
            name="batch_process",
            description="Process batches.",
            inputSchema={
                "properties": {
                    "batches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                    },
                },
                "required": ["batches"],
            },
        )
        source = _tool_function_source(tool)
        # Nested arrays need JSON parsing
        assert "batches: Annotated[str" in source
        assert "JSON Schema:" in source
        assert (
            "batches_parsed = json.loads(batches) if isinstance(batches, str) else batches"
            in source
        )
        compile(source, "<test>", "exec")

    def test_complex_type_with_default(self):
        """Test that complex types with defaults are JSON-serialized."""
        tool = mcp.types.Tool(
            name="configure",
            inputSchema={
                "properties": {
                    "options": {
                        "type": "object",
                        "default": {"timeout": 30, "retry": True},
                    },
                },
            },
        )
        source = _tool_function_source(tool)
        # Default should be JSON string, not Python dict
        # pydantic_core.to_json produces compact JSON
        assert '= \'{"timeout":30,"retry":true}\'' in source
        # Should parse safely even with default
        assert "isinstance(options, str)" in source
        compile(source, "<test>", "exec")

    def test_name_collision_detection(self):
        """Test that parameter name collisions are detected."""
        tool = mcp.types.Tool(
            name="test",
            inputSchema={
                "properties": {
                    "content-type": {"type": "string"},
                    "content_type": {"type": "string"},
                },
            },
        )
        # Should raise ValueError for collision
        with pytest.raises(ValueError, match="both sanitize to 'content_type'"):
            _tool_function_source(tool)


# ---------------------------------------------------------------------------
# _derive_server_name
# ---------------------------------------------------------------------------


class TestDeriveServerName:
    def test_bare_name(self):
        assert _derive_server_name("weather") == "weather"

    def test_qualified_name(self):
        assert _derive_server_name("cursor:weather") == "weather"

    def test_python_file(self):
        assert _derive_server_name("server.py") == "server"

    def test_url(self):
        assert _derive_server_name("http://localhost:8000/mcp") == "localhost"

    def test_trailing_colon(self):
        assert _derive_server_name("source:") == "source"


# ---------------------------------------------------------------------------
# generate_cli_script — produces compilable Python
# ---------------------------------------------------------------------------


class TestGenerateCliScript:
    def _make_tools(self) -> list[mcp.types.Tool]:
        return [
            mcp.types.Tool(
                name="greet",
                description="Say hello",
                inputSchema={
                    "properties": {
                        "name": {"type": "string", "description": "Who to greet"},
                    },
                    "required": ["name"],
                },
            ),
            mcp.types.Tool(
                name="add_numbers",
                description="Add two numbers",
                inputSchema={
                    "properties": {
                        "a": {"type": "integer", "description": "First number"},
                        "b": {"type": "integer", "description": "Second number"},
                    },
                    "required": ["a", "b"],
                },
            ),
        ]

    def test_compiles(self):
        script = generate_cli_script(
            server_name="test",
            server_spec="test",
            transport_code='"http://localhost:8000/mcp"',
            extra_imports=set(),
            tools=self._make_tools(),
        )
        compile(script, "<generated>", "exec")

    def test_contains_tool_functions(self):
        script = generate_cli_script(
            server_name="test",
            server_spec="test",
            transport_code='"http://localhost:8000/mcp"',
            extra_imports=set(),
            tools=self._make_tools(),
        )
        assert "async def greet(" in script
        assert "async def add_numbers(" in script

    def test_contains_generic_commands(self):
        script = generate_cli_script(
            server_name="test",
            server_spec="test",
            transport_code='"http://localhost:8000/mcp"',
            extra_imports=set(),
            tools=[],
        )
        assert "async def list_tools(" in script
        assert "async def list_resources(" in script
        assert "async def list_prompts(" in script
        assert "async def read_resource(" in script
        assert "async def get_prompt(" in script

    def test_embeds_transport(self):
        script = generate_cli_script(
            server_name="test",
            server_spec="test",
            transport_code="StdioTransport(command='fastmcp', args=['run', 'x.py'])",
            extra_imports={"from fastmcp.client.transports import StdioTransport"},
            tools=[],
        )
        assert "StdioTransport(command='fastmcp'" in script
        assert "from fastmcp.client.transports import StdioTransport" in script

    def test_no_tools_still_valid(self):
        script = generate_cli_script(
            server_name="empty",
            server_spec="empty",
            transport_code='"http://localhost"',
            extra_imports=set(),
            tools=[],
        )
        compile(script, "<generated>", "exec")
        assert "call_tool_app" in script

    def test_server_name_with_quotes(self):
        """Test that server names with quotes are properly escaped."""
        script = generate_cli_script(
            server_name='Test "Server" Name',
            server_spec="test",
            transport_code='"http://localhost"',
            extra_imports=set(),
            tools=[],
        )
        # Should compile without syntax errors
        compile(script, "<generated>", "exec")
        # App name should have escaped quotes
        assert r'app = cyclopts.App(name="test-\"server\"-name"' in script

    def test_compiles_with_unusual_names(self):
        tools = [
            mcp.types.Tool(
                name="my.tool/v2",
                description="A tool with dots and slashes",
                inputSchema={
                    "properties": {
                        "content-type": {"type": "string", "description": "CT"},
                    },
                    "required": ["content-type"],
                },
            ),
        ]
        script = generate_cli_script(
            server_name="test",
            server_spec="test",
            transport_code='"http://localhost:8000/mcp"',
            extra_imports=set(),
            tools=tools,
        )
        compile(script, "<generated>", "exec")

    def test_compiles_with_stdio_transport(self):
        transport = StdioTransport(command="fastmcp", args=["run", "server.py"])
        transport_code, extra_imports = serialize_transport(transport)
        script = generate_cli_script(
            server_name="test",
            server_spec="server.py",
            transport_code=transport_code,
            extra_imports=extra_imports,
            tools=self._make_tools(),
        )
        compile(script, "<generated>", "exec")


# ---------------------------------------------------------------------------
# generate_cli_command — integration tests
# ---------------------------------------------------------------------------


def _build_test_server() -> FastMCP:
    """Create a minimal FastMCP server for integration tests."""
    server = FastMCP("TestServer")

    @server.tool
    def greet(name: str) -> str:
        """Say hello to someone."""
        return f"Hello, {name}!"

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.resource("test://greeting")
    def greeting_resource() -> str:
        """A static greeting resource."""
        return "Hello from resource!"

    @server.prompt
    def ask(topic: str) -> str:
        """Ask about a topic."""
        return f"Tell me about {topic}"

    return server


@pytest.fixture()
def _patch_client():
    """Patch resolve_server_spec and _build_client to use an in-process server."""
    server = _build_test_server()

    def fake_resolve(server_spec: Any, **kwargs: Any) -> str:
        return "fake://server"

    def fake_build_client(resolved: Any, **kwargs: Any) -> Client:
        return Client(server)

    with (
        patch.object(generate_module, "resolve_server_spec", side_effect=fake_resolve),
        patch.object(generate_module, "_build_client", side_effect=fake_build_client),
    ):
        yield


class TestGenerateCliCommand:
    @pytest.mark.usefixtures("_patch_client")
    async def test_writes_file(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        await generate_cli_command("test-server", str(output))
        assert output.exists()
        content = output.read_text()
        compile(content, str(output), "exec")

    @pytest.mark.usefixtures("_patch_client")
    async def test_contains_tools(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        await generate_cli_command("test-server", str(output))
        content = output.read_text()
        assert "async def greet(" in content
        assert "async def add(" in content

    @pytest.mark.usefixtures("_patch_client")
    async def test_default_output_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.chdir(tmp_path)
        await generate_cli_command("test-server")
        assert (tmp_path / "cli.py").exists()

    @pytest.mark.usefixtures("_patch_client")
    async def test_error_if_exists(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        output.write_text("existing")
        with pytest.raises(SystemExit):
            await generate_cli_command("test-server", str(output))

    @pytest.mark.usefixtures("_patch_client")
    async def test_force_overwrites(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        output.write_text("existing")
        await generate_cli_command("test-server", str(output), force=True)
        content = output.read_text()
        assert content != "existing"
        assert "async def greet(" in content

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Unix executable bits N/A on Windows"
    )
    @pytest.mark.usefixtures("_patch_client")
    async def test_file_is_executable(self, tmp_path: Path):
        output = tmp_path / "cli.py"
        await generate_cli_command("test-server", str(output))
        assert output.stat().st_mode & 0o111
