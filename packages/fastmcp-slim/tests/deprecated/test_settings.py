import pytest

from fastmcp import FastMCP


class TestRemovedServerInitKwargs:
    """Test that removed server initialization keyword arguments raise TypeError."""

    @pytest.mark.parametrize(
        "kwarg, value, expected_message",
        [
            ("host", "0.0.0.0", "run_http_async"),
            ("port", 8080, "run_http_async"),
            ("sse_path", "/custom-sse", "FASTMCP_SSE_PATH"),
            ("message_path", "/custom-message", "FASTMCP_MESSAGE_PATH"),
            ("streamable_http_path", "/custom-http", "run_http_async"),
            ("json_response", True, "run_http_async"),
            ("stateless_http", True, "run_http_async"),
            ("debug", True, "FASTMCP_DEBUG"),
            ("log_level", "DEBUG", "run_http_async"),
            ("on_duplicate_tools", "warn", "on_duplicate="),
            ("on_duplicate_resources", "error", "on_duplicate="),
            ("on_duplicate_prompts", "replace", "on_duplicate="),
            ("tool_serializer", lambda x: str(x), "ToolResult"),
            ("include_tags", {"public"}, "server.enable"),
            ("exclude_tags", {"internal"}, "server.disable"),
            (
                "tool_transformations",
                {"my_tool": {"name": "renamed"}},
                "server.add_transform",
            ),
        ],
    )
    def test_removed_kwarg_raises_type_error(self, kwarg, value, expected_message):
        with pytest.raises(TypeError, match=f"no longer accepts `{kwarg}`"):
            FastMCP("TestServer", **{kwarg: value})

    @pytest.mark.parametrize(
        "kwarg, value, expected_message",
        [
            ("host", "0.0.0.0", "run_http_async"),
            ("on_duplicate_tools", "warn", "on_duplicate="),
            ("include_tags", {"public"}, "server.enable"),
        ],
    )
    def test_removed_kwarg_error_includes_migration_hint(
        self, kwarg, value, expected_message
    ):
        with pytest.raises(TypeError, match=expected_message):
            FastMCP("TestServer", **{kwarg: value})

    def test_unknown_kwarg_raises_standard_type_error(self):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            FastMCP("TestServer", **{"totally_fake_param": True})  # ty: ignore[invalid-argument-type]

    def test_valid_kwargs_still_work(self):
        server = FastMCP(
            name="TestServer",
            instructions="Test instructions",
            on_duplicate="warn",
            mask_error_details=True,
        )
        assert server.name == "TestServer"
        assert server.instructions == "Test instructions"
