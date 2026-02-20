"""Tests for fastmcp list and fastmcp call CLI commands."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import mcp.types
import pytest

from fastmcp import FastMCP
from fastmcp.cli import client as client_module
from fastmcp.cli.client import (
    Client,
    _build_client,
    _build_stdio_from_command,
    _format_call_result_text,
    _is_http_target,
    call_command,
    coerce_value,
    format_tool_signature,
    list_command,
    parse_tool_arguments,
    resolve_server_spec,
)
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports.stdio import StdioTransport

# ---------------------------------------------------------------------------
# coerce_value
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_integer(self):
        assert coerce_value("42", {"type": "integer"}) == 42

    def test_integer_negative(self):
        assert coerce_value("-7", {"type": "integer"}) == -7

    def test_integer_invalid(self):
        with pytest.raises(ValueError, match="Expected integer"):
            coerce_value("abc", {"type": "integer"})

    def test_number(self):
        assert coerce_value("3.14", {"type": "number"}) == 3.14

    def test_number_integer_value(self):
        assert coerce_value("5", {"type": "number"}) == 5.0

    def test_number_invalid(self):
        with pytest.raises(ValueError, match="Expected number"):
            coerce_value("xyz", {"type": "number"})

    def test_boolean_true_variants(self):
        for val in ("true", "True", "TRUE", "1", "yes"):
            assert coerce_value(val, {"type": "boolean"}) is True

    def test_boolean_false_variants(self):
        for val in ("false", "False", "FALSE", "0", "no"):
            assert coerce_value(val, {"type": "boolean"}) is False

    def test_boolean_invalid(self):
        with pytest.raises(ValueError, match="Expected boolean"):
            coerce_value("maybe", {"type": "boolean"})

    def test_array(self):
        assert coerce_value("[1, 2, 3]", {"type": "array"}) == [1, 2, 3]

    def test_array_invalid(self):
        with pytest.raises(ValueError, match="Expected JSON array"):
            coerce_value("not-json", {"type": "array"})

    def test_object(self):
        assert coerce_value('{"a": 1}', {"type": "object"}) == {"a": 1}

    def test_string(self):
        assert coerce_value("hello", {"type": "string"}) == "hello"

    def test_string_default(self):
        """Unknown or missing type treats value as string."""
        assert coerce_value("hello", {}) == "hello"

    def test_string_preserves_numeric_looking_values(self):
        assert coerce_value("42", {"type": "string"}) == "42"


# ---------------------------------------------------------------------------
# parse_tool_arguments
# ---------------------------------------------------------------------------


class TestParseToolArguments:
    SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "verbose": {"type": "boolean"},
        },
        "required": ["query"],
    }

    def test_basic_key_value(self):
        result = parse_tool_arguments(("query=hello", "limit=10"), None, self.SCHEMA)
        assert result == {"query": "hello", "limit": 10}

    def test_input_json_only(self):
        result = parse_tool_arguments((), '{"query": "hello", "limit": 5}', self.SCHEMA)
        assert result == {"query": "hello", "limit": 5}

    def test_key_value_overrides_input_json(self):
        result = parse_tool_arguments(
            ("limit=20",), '{"query": "hello", "limit": 5}', self.SCHEMA
        )
        assert result == {"query": "hello", "limit": 20}

    def test_value_containing_equals(self):
        result = parse_tool_arguments(("query=a=b=c",), None, self.SCHEMA)
        assert result == {"query": "a=b=c"}

    def test_invalid_arg_format_exits(self):
        with pytest.raises(SystemExit):
            parse_tool_arguments(("noequalssign",), None, self.SCHEMA)

    def test_invalid_input_json_exits(self):
        with pytest.raises(SystemExit):
            parse_tool_arguments((), "not-valid-json", self.SCHEMA)

    def test_input_json_non_object_exits(self):
        with pytest.raises(SystemExit):
            parse_tool_arguments((), "[1,2,3]", self.SCHEMA)

    def test_single_json_object_as_positional(self):
        result = parse_tool_arguments(
            ('{"query": "hello", "limit": 5}',), None, self.SCHEMA
        )
        assert result == {"query": "hello", "limit": 5}

    def test_json_positional_ignored_when_input_json_set(self):
        """When --input-json is already provided, a JSON positional arg is not special."""
        with pytest.raises(SystemExit):
            parse_tool_arguments(('{"limit": 99}',), '{"query": "hello"}', self.SCHEMA)

    def test_coercion_error_exits(self):
        with pytest.raises(SystemExit):
            parse_tool_arguments(("limit=abc",), None, self.SCHEMA)


# ---------------------------------------------------------------------------
# format_tool_signature
# ---------------------------------------------------------------------------


class TestFormatToolSignature:
    def _make_tool(
        self,
        name: str = "my_tool",
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
        output_schema: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> mcp.types.Tool:
        input_schema: dict[str, Any] = {"type": "object"}
        if properties is not None:
            input_schema["properties"] = properties
        if required is not None:
            input_schema["required"] = required
        return mcp.types.Tool(
            name=name,
            description=description,
            inputSchema=input_schema,
            outputSchema=output_schema,
        )

    def test_no_params(self):
        tool = self._make_tool()
        assert format_tool_signature(tool) == "my_tool()"

    def test_required_param(self):
        tool = self._make_tool(
            properties={"query": {"type": "string"}},
            required=["query"],
        )
        assert format_tool_signature(tool) == "my_tool(query: str)"

    def test_optional_param_with_default(self):
        tool = self._make_tool(
            properties={"limit": {"type": "integer", "default": 10}},
        )
        assert format_tool_signature(tool) == "my_tool(limit: int = 10)"

    def test_optional_param_without_default(self):
        tool = self._make_tool(
            properties={"limit": {"type": "integer"}},
        )
        assert format_tool_signature(tool) == "my_tool(limit: int = ...)"

    def test_mixed_required_and_optional(self):
        tool = self._make_tool(
            properties={
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            required=["query"],
        )
        sig = format_tool_signature(tool)
        assert sig == "my_tool(query: str, limit: int = 10)"

    def test_with_output_schema(self):
        tool = self._make_tool(
            properties={"q": {"type": "string"}},
            required=["q"],
            output_schema={"type": "object"},
        )
        assert format_tool_signature(tool) == "my_tool(q: str) -> dict"

    def test_anyof_type(self):
        tool = self._make_tool(
            properties={"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
            required=["value"],
        )
        assert format_tool_signature(tool) == "my_tool(value: str | int)"


# ---------------------------------------------------------------------------
# resolve_server_spec
# ---------------------------------------------------------------------------


class TestResolveServerSpec:
    def test_http_url(self):
        assert (
            resolve_server_spec("http://localhost:8000/mcp")
            == "http://localhost:8000/mcp"
        )

    def test_https_url(self):
        assert (
            resolve_server_spec("https://example.com/mcp") == "https://example.com/mcp"
        )

    def test_python_file_existing(self, tmp_path: Path):
        py_file = tmp_path / "server.py"
        py_file.write_text("# empty")
        result = resolve_server_spec(str(py_file))
        assert isinstance(result, StdioTransport)
        assert result.command == "fastmcp"
        assert result.args == ["run", str(py_file.resolve()), "--no-banner"]

    def test_json_mcp_config(self, tmp_path: Path):
        config_file = tmp_path / "mcp.json"
        config = {"mcpServers": {"test": {"url": "http://localhost:8000"}}}
        config_file.write_text(json.dumps(config))
        result = resolve_server_spec(str(config_file))
        assert isinstance(result, dict)
        assert "mcpServers" in result

    def test_json_fastmcp_config_exits(self, tmp_path: Path):
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"source": {"type": "file"}}))
        with pytest.raises(SystemExit):
            resolve_server_spec(str(config_file))

    def test_json_not_found_exits(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            resolve_server_spec(str(tmp_path / "nonexistent.json"))

    def test_directory_exits(self, tmp_path: Path):
        """Directories should not be treated as file paths."""
        with pytest.raises(SystemExit):
            resolve_server_spec(str(tmp_path))

    def test_unrecognised_exits(self):
        with pytest.raises(SystemExit):
            resolve_server_spec("some_random_thing")

    def test_command_returns_stdio_transport(self):
        result = resolve_server_spec(None, command="npx -y @mcp/server")
        assert isinstance(result, StdioTransport)
        assert result.command == "npx"
        assert result.args == ["-y", "@mcp/server"]

    def test_command_single_word(self):
        result = resolve_server_spec(None, command="myserver")
        assert isinstance(result, StdioTransport)
        assert result.command == "myserver"
        assert result.args == []

    def test_server_spec_and_command_exits(self):
        with pytest.raises(SystemExit):
            resolve_server_spec("http://localhost:8000", command="npx server")

    def test_neither_server_spec_nor_command_exits(self):
        with pytest.raises(SystemExit):
            resolve_server_spec(None)

    def test_transport_sse_rewrites_url(self):
        result = resolve_server_spec("http://localhost:8000/mcp", transport="sse")
        assert result == "http://localhost:8000/mcp/sse"

    def test_transport_sse_no_duplicate_suffix(self):
        result = resolve_server_spec("http://localhost:8000/sse", transport="sse")
        assert result == "http://localhost:8000/sse"

    def test_transport_sse_trailing_slash(self):
        result = resolve_server_spec("http://localhost:8000/mcp/", transport="sse")
        assert result == "http://localhost:8000/mcp/sse"

    def test_transport_http_leaves_url_unchanged(self):
        result = resolve_server_spec("http://localhost:8000/mcp", transport="http")
        assert result == "http://localhost:8000/mcp"


# ---------------------------------------------------------------------------
# _build_stdio_from_command
# ---------------------------------------------------------------------------


class TestBuildStdioFromCommand:
    def test_simple_command(self):
        transport = _build_stdio_from_command("uvx my-server")
        assert transport.command == "uvx"
        assert transport.args == ["my-server"]

    def test_quoted_args(self):
        transport = _build_stdio_from_command("npx -y '@scope/server'")
        assert transport.command == "npx"
        assert transport.args == ["-y", "@scope/server"]

    def test_empty_command_exits(self):
        with pytest.raises(SystemExit):
            _build_stdio_from_command("")

    def test_invalid_shell_syntax_exits(self):
        with pytest.raises(SystemExit):
            _build_stdio_from_command("npx 'unterminated")


# ---------------------------------------------------------------------------
# _is_http_target
# ---------------------------------------------------------------------------


class TestIsHttpTarget:
    def test_http_url(self):
        assert _is_http_target("http://localhost:8000") is True

    def test_https_url(self):
        assert _is_http_target("https://example.com/mcp") is True

    def test_file_path(self):
        assert _is_http_target("/path/to/server.py") is False

    def test_stdio_transport(self):
        assert _is_http_target(StdioTransport(command="npx", args=[])) is False

    def test_mcp_config_dict(self):
        """MCPConfig dicts are not HTTP targets — auth is per-server internally."""
        assert _is_http_target({"mcpServers": {}}) is False


# ---------------------------------------------------------------------------
# _build_client
# ---------------------------------------------------------------------------


class TestBuildClient:
    def test_http_target_gets_oauth_by_default(self):
        client = _build_client("http://localhost:8000/mcp")
        # OAuth is applied during Client init via _set_auth
        assert client.transport.auth is not None

    def test_stdio_target_no_auth(self):
        transport = StdioTransport(command="npx", args=["-y", "@mcp/server"])
        client = _build_client(transport)
        # Stdio transports don't support auth — no auth should be set
        assert not hasattr(client.transport, "auth") or client.transport.auth is None

    def test_explicit_auth_none_disables_oauth(self):
        client = _build_client("http://localhost:8000/mcp", auth="none")
        # "none" explicitly disables auth, even for HTTP targets
        assert client.transport.auth is None

    def test_mcp_config_no_auth(self):
        """MCPConfig dicts handle auth per-server; no top-level auth applied."""
        client = _build_client({"mcpServers": {"test": {"url": "http://localhost"}}})
        # MCPConfigTransport doesn't support _set_auth — no crash means success
        assert client.transport is not None


# ---------------------------------------------------------------------------
# Integration tests — invoke actual CLI commands via monkeypatched _build_client
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
    """Patch resolve_server_spec and _build_client so CLI commands use the
    in-process test server without needing a real transport."""
    server = _build_test_server()

    def fake_resolve(server_spec: Any, **kwargs: Any) -> str:
        return "fake"

    def fake_build_client(resolved: Any, **kwargs: Any) -> Client:
        return Client(server)

    with (
        patch.object(client_module, "resolve_server_spec", side_effect=fake_resolve),
        patch.object(client_module, "_build_client", side_effect=fake_build_client),
    ):
        yield


class TestListCommandCLI:
    @pytest.mark.usefixtures("_patch_client")
    async def test_list_tools(self, capsys: pytest.CaptureFixture[str]):
        await list_command("fake://server")
        captured = capsys.readouterr()
        assert "greet" in captured.out
        assert "add" in captured.out

    @pytest.mark.usefixtures("_patch_client")
    async def test_list_json(self, capsys: pytest.CaptureFixture[str]):
        await list_command("fake://server", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        names = {t["name"] for t in data["tools"]}
        assert "greet" in names
        assert "add" in names

    @pytest.mark.usefixtures("_patch_client")
    async def test_list_resources(self, capsys: pytest.CaptureFixture[str]):
        await list_command("fake://server", resources=True)
        captured = capsys.readouterr()
        assert "test://greeting" in captured.out

    @pytest.mark.usefixtures("_patch_client")
    async def test_list_prompts(self, capsys: pytest.CaptureFixture[str]):
        await list_command("fake://server", prompts=True)
        captured = capsys.readouterr()
        assert "ask" in captured.out


class TestCallCommandCLI:
    @pytest.mark.usefixtures("_patch_client")
    async def test_call_tool(self, capsys: pytest.CaptureFixture[str]):
        await call_command("fake://server", "greet", "name=World")
        captured = capsys.readouterr()
        assert "Hello, World!" in captured.out

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_tool_json(self, capsys: pytest.CaptureFixture[str]):
        await call_command("fake://server", "greet", "name=World", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["is_error"] is False

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_tool_not_found(self):
        with pytest.raises(SystemExit):
            await call_command("fake://server", "nonexistent")

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_tool_missing_args(self):
        with pytest.raises(SystemExit):
            await call_command("fake://server", "greet")

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_resource_by_uri(self, capsys: pytest.CaptureFixture[str]):
        await call_command("fake://server", "test://greeting")
        captured = capsys.readouterr()
        assert "Hello from resource!" in captured.out

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_resource_json(self, capsys: pytest.CaptureFixture[str]):
        await call_command("fake://server", "test://greeting", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert data[0]["text"] == "Hello from resource!"

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_prompt(self, capsys: pytest.CaptureFixture[str]):
        await call_command("fake://server", "ask", "topic=Python", prompt=True)
        captured = capsys.readouterr()
        assert "Python" in captured.out

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_prompt_json(self, capsys: pytest.CaptureFixture[str]):
        await call_command(
            "fake://server", "ask", "topic=Python", prompt=True, json_output=True
        )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "messages" in data

    @pytest.mark.usefixtures("_patch_client")
    async def test_call_prompt_not_found(self):
        with pytest.raises(SystemExit):
            await call_command("fake://server", "nonexistent", prompt=True)

    async def test_call_missing_target(self):
        with pytest.raises(SystemExit):
            await call_command("fake://server", "")


# ---------------------------------------------------------------------------
# Structured content serialization
# ---------------------------------------------------------------------------


class TestFormatCallResult:
    def test_structured_content_uses_dict_not_data(
        self, capsys: pytest.CaptureFixture[str]
    ):
        """structured_content (raw dict) is used for display, not data (which may
        be a non-serializable dataclass)."""
        result = CallToolResult(
            content=[mcp.types.TextContent(type="text", text="ok")],
            structured_content={"key": "value"},
            meta=None,
            data=object(),  # non-serializable on purpose
            is_error=False,
        )
        # Should not raise — uses structured_content, not data
        _format_call_result_text(result)
        captured = capsys.readouterr()
        assert "value" in captured.out
