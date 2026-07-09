"""Tests for the MCP SDK v2 camelCase compatibility bridge (fastmcp._compat)."""

import datetime
import warnings

import mcp_types
import pytest
from mcp import MCPError as SDKMCPError

import fastmcp
import fastmcp._compat as _compat
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import FastMCPDeprecationWarning, MCPError, McpError


def _reset_warn_once() -> None:
    """Reinstall the shims so every property's warn-once flag starts fresh.

    Each bridged property closes over its own `warned` flag which persists for
    the life of the process. Rebuilding the properties gives tests a clean slate
    without leaking warning state across tests.
    """
    for cls, mapping in _compat._ALIASES.items():
        for camel in mapping:
            attr = cls.__dict__.get(camel)
            if isinstance(attr, property):
                delattr(cls, camel)
    _compat._installed = False
    _compat.install()


@pytest.fixture(autouse=True)
def fresh_shims():
    _reset_warn_once()
    yield


class TestCamelCaseBridge:
    def test_tool_input_schema_bridged(self):
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
        with pytest.warns(FastMCPDeprecationWarning):
            assert tool.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]

    def test_tool_output_schema_bridged(self):
        tool = mcp_types.Tool(
            name="t",
            input_schema={"type": "object"},
            output_schema={"type": "string"},
        )
        with pytest.warns(FastMCPDeprecationWarning):
            assert tool.outputSchema == {"type": "string"}  # ty: ignore[unresolved-attribute]

    def test_call_tool_result_is_error_bridged(self):
        result = mcp_types.CallToolResult(content=[], is_error=True)
        with pytest.warns(FastMCPDeprecationWarning):
            assert result.isError is True  # ty: ignore[unresolved-attribute]

    def test_call_tool_result_structured_content_bridged(self):
        result = mcp_types.CallToolResult(content=[], structured_content={"a": 1})
        with pytest.warns(FastMCPDeprecationWarning):
            assert result.structuredContent == {"a": 1}  # ty: ignore[unresolved-attribute]

    def test_resource_mime_type_bridged(self):
        resource = mcp_types.Resource(name="r", uri="file:///x", mime_type="text/plain")
        with pytest.warns(FastMCPDeprecationWarning):
            assert resource.mimeType == "text/plain"  # ty: ignore[unresolved-attribute]

    def test_resource_template_uri_template_bridged(self):
        template = mcp_types.ResourceTemplate(name="rt", uri_template="file:///{id}")
        with pytest.warns(FastMCPDeprecationWarning):
            assert template.uriTemplate == "file:///{id}"  # ty: ignore[unresolved-attribute]

    def test_completion_has_more_bridged(self):
        completion = mcp_types.Completion(values=["a"], has_more=True)
        with pytest.warns(FastMCPDeprecationWarning):
            assert completion.hasMore is True  # ty: ignore[unresolved-attribute]

    def test_list_tools_result_next_cursor_bridged(self):
        result = mcp_types.ListToolsResult(tools=[], next_cursor="abc")
        with pytest.warns(FastMCPDeprecationWarning):
            assert result.nextCursor == "abc"  # ty: ignore[unresolved-attribute]

    def test_sampling_params_max_tokens_bridged(self):
        params = mcp_types.CreateMessageRequestParams(messages=[], max_tokens=100)
        with pytest.warns(FastMCPDeprecationWarning):
            assert params.maxTokens == 100  # ty: ignore[unresolved-attribute]

    def test_elicit_form_params_requested_schema_bridged(self):
        params = mcp_types.ElicitRequestFormParams(
            message="hi", requested_schema={"type": "object", "properties": {}}
        )
        with pytest.warns(FastMCPDeprecationWarning):
            assert params.requestedSchema == {"type": "object", "properties": {}}  # ty: ignore[unresolved-attribute]


class TestWarnOnce:
    def test_warns_exactly_once_per_class_name(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            t1 = mcp_types.Tool(name="a", input_schema={"type": "object"})
            t2 = mcp_types.Tool(name="b", input_schema={"type": "string"})
            _ = t1.inputSchema  # ty: ignore[unresolved-attribute]
            _ = t2.inputSchema  # ty: ignore[unresolved-attribute]
            _ = t1.inputSchema  # ty: ignore[unresolved-attribute]
        deprecations = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert len(deprecations) == 1

    def test_distinct_names_warn_independently(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tool = mcp_types.Tool(
                name="a",
                input_schema={"type": "object"},
                output_schema={"type": "string"},
            )
            _ = tool.inputSchema  # ty: ignore[unresolved-attribute]
            _ = tool.outputSchema  # ty: ignore[unresolved-attribute]
        deprecations = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert len(deprecations) == 2


class TestModelRoundTrip:
    def test_survives_model_copy(self):
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
        copied = tool.model_copy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert copied.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]

    def test_survives_model_validate(self):
        tool = mcp_types.Tool.model_validate(
            {"name": "t", "inputSchema": {"type": "object"}}
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert tool.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]
            assert tool.input_schema == {"type": "object"}


class TestGuards:
    def test_does_not_shadow_existing_snake_field(self):
        # The real snake_case field must always resolve without warning.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
            assert tool.input_schema == {"type": "object"}
        assert not caught

    def test_install_is_idempotent(self):
        _compat.install()
        _compat.install()
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
        with pytest.warns(FastMCPDeprecationWarning):
            assert tool.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]


class TestSettingOff:
    def test_setting_off_raises_attribute_error(self, monkeypatch):
        # The property stays installed, but with the setting disabled the getter
        # raises AttributeError as if the camelCase name never existed.
        monkeypatch.setattr(fastmcp.settings, "mcp_camelcase_compat", False)
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
        with pytest.raises(AttributeError):
            _ = tool.inputSchema  # ty: ignore[unresolved-attribute]

    def test_setting_off_attribute_error_message(self, monkeypatch):
        monkeypatch.setattr(fastmcp.settings, "mcp_camelcase_compat", False)
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})
        with pytest.raises(
            AttributeError,
            match=r"'Tool' object has no attribute 'inputSchema'",
        ):
            _ = tool.inputSchema  # ty: ignore[unresolved-attribute]

    def test_runtime_toggle_on_off_on(self, monkeypatch):
        # The bridge honours the live setting on every read: on -> off -> on.
        tool = mcp_types.Tool(name="t", input_schema={"type": "object"})

        # On (default): bridged read works and warns.
        with pytest.warns(FastMCPDeprecationWarning):
            assert tool.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]

        # Off: same attribute now raises.
        monkeypatch.setattr(fastmcp.settings, "mcp_camelcase_compat", False)
        with pytest.raises(AttributeError):
            _ = tool.inputSchema  # ty: ignore[unresolved-attribute]

        # Back on: resolves again (warn-once may have fired already, so ignore).
        monkeypatch.setattr(fastmcp.settings, "mcp_camelcase_compat", True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert tool.inputSchema == {"type": "object"}  # ty: ignore[unresolved-attribute]


class TestExceptionAlias:
    def test_mcp_error_is_alias(self):
        assert McpError is MCPError
        assert McpError is SDKMCPError

    def test_except_mcp_error_catches_sdk_raised(self):
        with pytest.raises(McpError):
            raise SDKMCPError(code=-32000, message="boom")


class TestClientBehaviorCompat:
    """Behavior compat checklist (design decision D)."""

    @pytest.fixture
    def server(self):
        srv = FastMCP("BehaviorServer")

        @srv.tool
        def echo(x: str) -> str:
            return x

        return srv

    @pytest.mark.parametrize(
        "timeout",
        [5, 5.0, datetime.timedelta(seconds=5), None],
    )
    async def test_client_accepts_timedelta_or_float_timeout(self, server, timeout):
        client = Client(transport=FastMCPTransport(server), timeout=timeout)
        async with client:
            result = await client.call_tool("echo", {"x": "hi"})
        assert result.data == "hi"

    async def test_ping_returns_bool(self, server):
        client = Client(transport=FastMCPTransport(server), mode="legacy")
        async with client:
            result = await client.ping()
        assert result is True

    async def test_session_id_none_safe(self, server):
        # In-memory transport has no HTTP session id; must return None, not raise.
        client = Client(transport=FastMCPTransport(server))
        async with client:
            assert client.transport.get_session_id() is None
