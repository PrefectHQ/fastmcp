import warnings
from unittest.mock import patch

import pytest

from fastmcp import FastMCP

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestDeprecatedServerInitKwargs:
    """Test deprecated server initialization keyword arguments."""

    def test_log_level_deprecation_warning(self):
        """Test that log_level raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `log_level` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", log_level="DEBUG")

        # Verify the setting is still applied
        assert server._deprecated_settings.log_level == "DEBUG"

    def test_debug_deprecation_warning(self):
        """Test that debug raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `debug` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", debug=True)

        # Verify the setting is still applied
        assert server._deprecated_settings.debug is True

    def test_host_deprecation_warning(self):
        """Test that host raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `host` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", host="0.0.0.0")

        # Verify the setting is still applied
        assert server._deprecated_settings.host == "0.0.0.0"

    def test_port_deprecation_warning(self):
        """Test that port raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `port` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", port=8080)

        # Verify the setting is still applied
        assert server._deprecated_settings.port == 8080

    def test_sse_path_deprecation_warning(self):
        """Test that sse_path raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `sse_path` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", sse_path="/custom-sse")

        # Verify the setting is still applied
        assert server._deprecated_settings.sse_path == "/custom-sse"

    def test_message_path_deprecation_warning(self):
        """Test that message_path raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `message_path` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", message_path="/custom-message")

        # Verify the setting is still applied
        assert server._deprecated_settings.message_path == "/custom-message"

    def test_streamable_http_path_deprecation_warning(self):
        """Test that streamable_http_path raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `streamable_http_path` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", streamable_http_path="/custom-http")

        # Verify the setting is still applied
        assert server._deprecated_settings.streamable_http_path == "/custom-http"

    def test_json_response_deprecation_warning(self):
        """Test that json_response raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `json_response` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", json_response=True)

        # Verify the setting is still applied
        assert server._deprecated_settings.json_response is True

    def test_stateless_http_deprecation_warning(self):
        """Test that stateless_http raises a deprecation warning."""
        with pytest.warns(
            DeprecationWarning,
            match=r"Providing `stateless_http` when creating a server is deprecated\. Provide it when calling `run` or as a global setting instead\.",
        ):
            server = FastMCP("TestServer", stateless_http=True)

        # Verify the setting is still applied
        assert server._deprecated_settings.stateless_http is True

    def test_multiple_deprecated_kwargs_warnings(self):
        """Test that multiple deprecated kwargs each raise their own warning."""
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            server = FastMCP(
                "TestServer",
                log_level="INFO",
                debug=False,
                host="127.0.0.1",
                port=9999,
                sse_path="/sse/",
                message_path="/msg",
                streamable_http_path="/http",
                json_response=False,
                stateless_http=False,
            )

        # Should have 9 deprecation warnings (one for each deprecated parameter)
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 9

        # Verify all expected parameters are mentioned in warnings
        expected_params = {
            "log_level",
            "debug",
            "host",
            "port",
            "sse_path",
            "message_path",
            "streamable_http_path",
            "json_response",
            "stateless_http",
        }
        mentioned_params = set()
        for warning in deprecation_warnings:
            message = str(warning.message)
            for param in expected_params:
                if f"Providing `{param}`" in message:
                    mentioned_params.add(param)

        assert mentioned_params == expected_params

        # Verify all settings are still applied
        assert server._deprecated_settings.log_level == "INFO"
        assert server._deprecated_settings.debug is False
        assert server._deprecated_settings.host == "127.0.0.1"
        assert server._deprecated_settings.port == 9999
        assert server._deprecated_settings.sse_path == "/sse/"
        assert server._deprecated_settings.message_path == "/msg"
        assert server._deprecated_settings.streamable_http_path == "/http"
        assert server._deprecated_settings.json_response is False
        assert server._deprecated_settings.stateless_http is False

    def test_non_deprecated_kwargs_no_warnings(self):
        """Test that non-deprecated kwargs don't raise warnings."""
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            server = FastMCP(
                name="TestServer",
                instructions="Test instructions",
                on_duplicate="warn",  # New unified parameter
                mask_error_details=True,
            )

        # Should have no deprecation warnings
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0

        # Verify server was created successfully
        assert server.name == "TestServer"
        assert server.instructions == "Test instructions"

    def test_deprecated_duplicate_kwargs_raise_warnings(self):
        """Test that deprecated on_duplicate_* kwargs raise warnings."""
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            FastMCP(
                name="TestServer",
                on_duplicate_tools="warn",
                on_duplicate_resources="error",
                on_duplicate_prompts="replace",
            )

        # Should have 3 deprecation warnings (one for each deprecated param)
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 3

        # Check warning messages
        warning_messages = [str(w.message) for w in deprecation_warnings]
        assert any("on_duplicate_tools" in msg for msg in warning_messages)
        assert any("on_duplicate_resources" in msg for msg in warning_messages)
        assert any("on_duplicate_prompts" in msg for msg in warning_messages)

    def test_none_values_no_warnings(self):
        """Test that None values for deprecated kwargs don't raise warnings."""
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            FastMCP(
                "TestServer",
                log_level=None,
                debug=None,
                host=None,
                port=None,
                sse_path=None,
                message_path=None,
                streamable_http_path=None,
                json_response=None,
                stateless_http=None,
            )

        # Should have no deprecation warnings for None values
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0

    def test_deprecated_settings_inheritance_from_global(self):
        """Test that deprecated settings inherit from global settings when not provided."""
        # Mock fastmcp.settings to test inheritance
        with patch("fastmcp.settings") as mock_settings:
            mock_settings.model_dump.return_value = {
                "log_level": "WARNING",
                "debug": True,
                "host": "0.0.0.0",
                "port": 3000,
                "sse_path": "/events",
                "message_path": "/messages",
                "streamable_http_path": "/stream",
                "json_response": True,
                "stateless_http": True,
            }

            server = FastMCP("TestServer")

            # Verify settings are inherited from global settings
            assert server._deprecated_settings.log_level == "WARNING"
            assert server._deprecated_settings.debug is True
            assert server._deprecated_settings.host == "0.0.0.0"
            assert server._deprecated_settings.port == 3000
            assert server._deprecated_settings.sse_path == "/events"
            assert server._deprecated_settings.message_path == "/messages"
            assert server._deprecated_settings.streamable_http_path == "/stream"
            assert server._deprecated_settings.json_response is True
            assert server._deprecated_settings.stateless_http is True

    def test_deprecated_settings_override_global(self):
        """Test that deprecated settings override global settings when provided."""
        # Mock fastmcp.settings to test override behavior
        with patch("fastmcp.settings") as mock_settings:
            mock_settings.model_dump.return_value = {
                "log_level": "WARNING",
                "debug": True,
                "host": "0.0.0.0",
                "port": 3000,
                "sse_path": "/events",
                "message_path": "/messages",
                "streamable_http_path": "/stream",
                "json_response": True,
                "stateless_http": True,
            }

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # Ignore warnings for this test
                server = FastMCP(
                    "TestServer",
                    log_level="ERROR",
                    debug=False,
                    host="127.0.0.1",
                    port=8080,
                )

            # Verify provided settings override global settings
            assert server._deprecated_settings.log_level == "ERROR"
            assert server._deprecated_settings.debug is False
            assert server._deprecated_settings.host == "127.0.0.1"
            assert server._deprecated_settings.port == 8080
            # Non-overridden settings should still come from global
            assert server._deprecated_settings.sse_path == "/events"
            assert server._deprecated_settings.message_path == "/messages"
            assert server._deprecated_settings.streamable_http_path == "/stream"
            assert server._deprecated_settings.json_response is True
            assert server._deprecated_settings.stateless_http is True

    def test_stacklevel_points_to_constructor_call(self):
        """Test that deprecation warnings point to the FastMCP constructor call."""
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")

            FastMCP("TestServer", log_level="DEBUG")

        # Should have exactly one deprecation warning
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 1

        # The warning should point to the server.py file where FastMCP.__init__ is called
        # This verifies the stacklevel is working as intended (pointing to constructor)
        warning = deprecation_warnings[0]
        assert "server.py" in warning.filename
