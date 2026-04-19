"""Tests for the exception hierarchy.

This module tests the comprehensive exception hierarchy defined in
src/fastmcp/exceptions.py, including:
- Inheritance relationships
- Default error codes
- to_dict() serialization
- to_mcp_error() conversion
- Backward compatibility
"""

import pytest
from mcp import McpError
from mcp.types import ErrorData

from fastmcp.exceptions import (
    AuthorizationError,
    FastMCPError,
    MCPAuthorizationError,
    MCPProtocolError,
    MCPResourceError,
    MCPPromptError,
    MCPToolError,
    MCPTransportError,
    MCPValidationError,
    NotFoundError,
    PromptError,
    PromptNotFoundError,
    ResourceAccessDeniedError,
    ResourceError,
    ResourceNotFoundError,
    ToolArgumentError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ValidationError,
)


class TestExceptionInheritance:
    """Test that all exceptions inherit correctly."""

    def test_all_exceptions_inherit_from_fastmcperror(self):
        """All exception types should inherit from FastMCPError."""
        exceptions = [
            MCPProtocolError,
            MCPTransportError,
            MCPToolError,
            MCPResourceError,
            MCPValidationError,
            MCPPromptError,
            MCPAuthorizationError,
            ToolError,
            ToolNotFoundError,
            ToolArgumentError,
            ToolExecutionError,
            ResourceError,
            ResourceNotFoundError,
            ResourceAccessDeniedError,
            ValidationError,
            NotFoundError,
            PromptError,
            PromptNotFoundError,
            AuthorizationError,
        ]

        for exc_class in exceptions:
            try:
                raise exc_class("test")
            except FastMCPError as e:
                assert isinstance(e, exc_class)

    def test_tool_exceptions_inheritance(self):
        """Test tool-related exception hierarchy."""
        assert issubclass(ToolError, MCPToolError)
        assert issubclass(ToolNotFoundError, ToolError)
        assert issubclass(ToolArgumentError, ToolError)
        assert issubclass(ToolExecutionError, ToolError)

    def test_resource_exceptions_inheritance(self):
        """Test resource-related exception hierarchy."""
        assert issubclass(ResourceError, MCPResourceError)
        assert issubclass(ResourceNotFoundError, ResourceError)
        assert issubclass(ResourceAccessDeniedError, ResourceError)

    def test_validation_exceptions_inheritance(self):
        """Test validation-related exception hierarchy."""
        assert issubclass(ValidationError, MCPValidationError)

    def test_prompt_exceptions_inheritance(self):
        """Test prompt-related exception hierarchy."""
        assert issubclass(PromptError, MCPPromptError)
        assert issubclass(PromptNotFoundError, PromptError)

    def test_authorization_exceptions_inheritance(self):
        """Test authorization-related exception hierarchy."""
        assert issubclass(AuthorizationError, MCPAuthorizationError)

    def test_notfound_is_independent(self):
        """NotFoundError is a generic not found error, independent of MCPResourceError."""
        assert issubclass(NotFoundError, FastMCPError)
        assert not issubclass(NotFoundError, MCPResourceError)

    def test_backward_compatibility_catch_all(self):
        """Existing code catching ToolError should also catch new tool subclasses."""
        try:
            raise ToolNotFoundError("tool not found")
        except ToolError:
            pass

        try:
            raise ToolArgumentError("bad argument")
        except ToolError:
            pass

        try:
            raise ToolExecutionError("execution failed")
        except ToolError:
            pass

    def test_backward_compatibility_catch_resource_error(self):
        """Existing code catching ResourceError should catch new resource subclasses."""
        try:
            raise ResourceNotFoundError("resource not found")
        except ResourceError:
            pass

        try:
            raise ResourceAccessDeniedError("access denied")
        except ResourceError:
            pass


class TestDefaultErrorCodes:
    """Test that each exception type has the correct default error code."""

    def test_fastmcperror_default_code(self):
        exc = FastMCPError("test")
        assert exc.code == -32603

    def test_protocol_error_codes(self):
        assert MCPProtocolError.code == -32600
        assert MCPTransportError.code == -32000

    def test_tool_error_codes(self):
        assert MCPToolError.code == -32603
        assert ToolError.code == -32603
        assert ToolNotFoundError.code == -32001
        assert ToolArgumentError.code == -32602
        assert ToolExecutionError.code == -32603

    def test_resource_error_codes(self):
        assert MCPResourceError.code == -32603
        assert ResourceError.code == -32603
        assert ResourceNotFoundError.code == -32002  # MCP spec-defined
        assert ResourceAccessDeniedError.code == -32000
        assert NotFoundError.code == -32001

    def test_validation_error_codes(self):
        assert MCPValidationError.code == -32602
        assert ValidationError.code == -32602

    def test_prompt_error_codes(self):
        assert MCPPromptError.code == -32603
        assert PromptError.code == -32603
        assert PromptNotFoundError.code == -32001

    def test_authorization_error_codes(self):
        assert MCPAuthorizationError.code == -32000
        assert AuthorizationError.code == -32000


class TestExceptionAttributes:
    """Test exception attributes and initialization."""

    def test_default_initialization(self):
        exc = FastMCPError()
        assert exc.message == "Internal error"
        assert exc.code == -32603
        assert exc.data == {}

    def test_custom_message(self):
        exc = FastMCPError("custom message")
        assert exc.message == "custom message"

    def test_custom_code(self):
        exc = FastMCPError("test", code=-12345)
        assert exc.code == -12345

    def test_custom_data(self):
        exc = FastMCPError("test", data={"key": "value", "details": 123})
        assert exc.data == {"key": "value", "details": 123}

    def test_full_init(self):
        exc = FastMCPError(
            message="custom message",
            code=-12345,
            data={"context": "test"},
        )
        assert exc.message == "custom message"
        assert exc.code == -12345
        assert exc.data == {"context": "test"}

    def test_str_representation(self):
        exc = FastMCPError("my message")
        assert str(exc) == "my message"

    def test_default_messages(self):
        """Test default messages for each exception type."""
        assert MCPProtocolError().message == "Protocol error"
        assert MCPTransportError().message == "Transport error"
        assert ToolNotFoundError().message == "Tool not found"
        assert ToolArgumentError().message == "Invalid tool arguments"
        assert ToolExecutionError().message == "Tool execution failed"
        assert ResourceNotFoundError().message == "Resource not found"
        assert ResourceAccessDeniedError().message == "Resource access denied"
        assert ValidationError().message == "Validation error"
        assert NotFoundError().message == "Not found"
        assert PromptNotFoundError().message == "Prompt not found"
        assert AuthorizationError().message == "Authorization failed"

    def test_custom_message_overrides_default(self):
        """Custom message should override class-level default."""
        exc = ToolNotFoundError("my_custom_tool")
        assert exc.message == "my_custom_tool"
        assert exc.code == -32001  # code stays at default


class TestToDictSerialization:
    """Test to_dict() method serializes to JSON-RPC error format."""

    def test_basic_to_dict(self):
        exc = FastMCPError("test message", code=-12345)
        result = exc.to_dict()

        assert result == {
            "code": -12345,
            "message": "test message",
        }

    def test_to_dict_with_data(self):
        exc = FastMCPError(
            "test",
            code=-12345,
            data={"details": "some info", "traceback": "..."},
        )
        result = exc.to_dict()

        assert result == {
            "code": -12345,
            "message": "test",
            "data": {"details": "some info", "traceback": "..."},
        }

    def test_to_dict_without_data(self):
        exc = FastMCPError("test", code=-12345, data={})
        result = exc.to_dict()

        assert "data" not in result

    def test_tool_error_to_dict(self):
        exc = ToolNotFoundError("my_tool")
        result = exc.to_dict()

        assert result == {
            "code": -32001,
            "message": "my_tool",
        }

    def test_resource_error_to_dict(self):
        exc = ResourceNotFoundError("file:///test.txt")
        result = exc.to_dict()

        assert result == {
            "code": -32002,
            "message": "file:///test.txt",
        }


class TestToMcpErrorConversion:
    """Test to_mcp_error() method converts to McpError type."""

    def test_basic_to_mcp_error(self):
        exc = FastMCPError("test message", code=-12345)
        result = exc.to_mcp_error()

        assert isinstance(result, McpError)
        assert isinstance(result.error, ErrorData)
        assert result.error.code == -12345
        assert result.error.message == "test message"

    def test_to_mcp_error_with_data(self):
        exc = FastMCPError(
            "test",
            code=-12345,
            data={"key": "value"},
        )
        result = exc.to_mcp_error()

        assert result.error.data == {"key": "value"}

    def test_tool_not_found_to_mcp_error(self):
        exc = ToolNotFoundError("my_tool")
        result = exc.to_mcp_error()

        assert result.error.code == -32001
        assert result.error.message == "my_tool"

    def test_invalid_args_to_mcp_error(self):
        exc = ToolArgumentError("expected integer, got string")
        result = exc.to_mcp_error()

        assert result.error.code == -32602

    def test_to_error_data(self):
        """Test the internal to_error_data method."""
        exc = FastMCPError("test", code=-12345, data={"a": 1})
        error_data = exc.to_error_data()

        assert isinstance(error_data, ErrorData)
        assert error_data.code == -12345
        assert error_data.message == "test"
        assert error_data.data == {"a": 1}


class TestExceptionCauseChain:
    """Test that exception cause chains are preserved."""

    def test_exception_with_cause(self):
        try:
            try:
                raise ValueError("original error")
            except ValueError as e:
                raise ToolExecutionError("wrapped error") from e
        except ToolExecutionError as exc:
            assert exc.__cause__ is not None
            assert isinstance(exc.__cause__, ValueError)
            assert str(exc.__cause__) == "original error"

    def test_exception_without_cause(self):
        exc = ToolExecutionError("error")
        assert exc.__cause__ is None

    def test_exception_chaining_via_raise_from(self):
        """Test explicit chaining with raise ... from ..."""
        original = TypeError("wrong type")
        try:
            raise ToolArgumentError("invalid args") from original
        except ToolArgumentError as exc:
            assert exc.__cause__ is original

    def test_exception_implicit_chaining(self):
        """Test implicit chaining (without 'from')."""
        try:
            try:
                raise ValueError("inner")
            except ValueError:
                raise ToolExecutionError("outer")
        except ToolExecutionError as exc:
            assert exc.__context__ is not None
            assert isinstance(exc.__context__, ValueError)


class TestBackwardCompatibility:
    """Test that existing exception types remain backward compatible."""

    def test_tool_error_is_mcptoolerror(self):
        exc = ToolError("test")
        assert isinstance(exc, MCPToolError)
        assert isinstance(exc, FastMCPError)

    def test_tool_error_type_name(self):
        """Ensure type name is 'ToolError' for backward compatibility."""
        exc = ToolError("test")
        assert type(exc).__name__ == "ToolError"

    def test_resource_error_type_name(self):
        exc = ResourceError("test")
        assert type(exc).__name__ == "ResourceError"

    def test_validation_error_type_name(self):
        exc = ValidationError("test")
        assert type(exc).__name__ == "ValidationError"

    def test_prompt_error_type_name(self):
        exc = PromptError("test")
        assert type(exc).__name__ == "PromptError"

    def test_authorization_error_type_name(self):
        exc = AuthorizationError("test")
        assert type(exc).__name__ == "AuthorizationError"

    def test_except_tool_error_catches_all_tool_exceptions(self):
        """Test that existing `except ToolError:` code works for new subclasses."""
        exception_classes = [
            ToolNotFoundError,
            ToolArgumentError,
            ToolExecutionError,
        ]

        for exc_class in exception_classes:
            try:
                raise exc_class("test")
            except ToolError:
                pass  # Should catch
            except Exception:
                pytest.fail(f"{exc_class.__name__} not caught by ToolError")

    def test_except_resource_error_catches_resource_subclasses(self):
        exception_classes = [
            ResourceNotFoundError,
            ResourceAccessDeniedError,
        ]

        for exc_class in exception_classes:
            try:
                raise exc_class("test")
            except ResourceError:
                pass
            except Exception:
                pytest.fail(f"{exc_class.__name__} not caught by ResourceError")

    def test_except_validation_error_catches_mcpvalidationerror(self):
        """Note: MCPValidationError is the base class, so it won't be caught
        by except ValidationError:. This is by design - use except FastMCPError:
        or except MCPValidationError: to catch all validation errors.
        """
        exc = MCPValidationError("test")
        assert isinstance(exc, FastMCPError)
        assert not isinstance(exc, ValidationError)  # ValidationError is subclass

    def test_except_mcpvalidationerror_catches_validationerror(self):
        """except MCPValidationError: should catch ValidationError."""
        try:
            raise ValidationError("test")
        except MCPValidationError:
            pass
        except Exception:
            pytest.fail("ValidationError not caught by MCPValidationError")


class TestJsonRpcCodeConventions:
    """Test that error codes follow JSON-RPC 2.0 conventions."""

    JSON_RPC_STANDARD_CODES = {
        -32700: "Parse error",
        -32600: "Invalid Request",
        -32601: "Method not found",
        -32602: "Invalid params",
        -32603: "Internal error",
    }

    def test_standard_jsonrpc_codes_used(self):
        """Test that standard JSON-RPC codes are used where appropriate."""
        assert ToolArgumentError.code == -32602  # Invalid params
        assert ValidationError.code == -32602  # Invalid params
        assert MCPValidationError.code == -32602  # Invalid params
        assert MCPProtocolError.code == -32600  # Invalid Request
        assert FastMCPError.code == -32603  # Internal error

    def test_server_error_range(self):
        """Test that server-defined errors are in -32000 to -32099 range."""
        server_error_codes = [
            MCPTransportError.code,
            ToolNotFoundError.code,
            ResourceNotFoundError.code,
            ResourceAccessDeniedError.code,
            NotFoundError.code,
            PromptNotFoundError.code,
            AuthorizationError.code,
            MCPAuthorizationError.code,
        ]

        for code in server_error_codes:
            assert -32099 <= code <= -32000, (
                f"Code {code} should be in server error range (-32099 to -32000)"
            )

    def test_no_code_outside_reserved_ranges(self):
        """Ensure no codes use pre-reserved ranges incorrectly."""
        all_exceptions = [
            FastMCPError,
            MCPProtocolError,
            MCPTransportError,
            MCPToolError,
            MCPResourceError,
            MCPValidationError,
            MCPPromptError,
            MCPAuthorizationError,
            ToolError,
            ToolNotFoundError,
            ToolArgumentError,
            ToolExecutionError,
            ResourceError,
            ResourceNotFoundError,
            ResourceAccessDeniedError,
            ValidationError,
            NotFoundError,
            PromptError,
            PromptNotFoundError,
            AuthorizationError,
        ]

        for exc in all_exceptions:
            code = exc.code
            if code in self.JSON_RPC_STANDARD_CODES:
                pass  # Standard codes are OK
            else:
                assert -32099 <= code <= -32000, (
                    f"{exc.__name__} code {code} should be either a standard "
                    "JSON-RPC code or in the server error range (-32099 to -32000)"
                )


class TestMCPErrorAlias:
    """Test that MCPError is an alias for FastMCPError."""

    def test_mcperror_is_fastmcperror(self):
        """MCPError should be an alias for FastMCPError for backward compatibility."""
        from fastmcp.exceptions import MCPError

        assert MCPError is FastMCPError

    def test_mcperror_catches_all(self):
        """except MCPError: should catch all FastMCPError subclasses."""
        from fastmcp.exceptions import MCPError

        try:
            raise ToolNotFoundError("test")
        except MCPError:
            pass

        try:
            raise ResourceNotFoundError("test")
        except MCPError:
            pass

        try:
            raise ValidationError("test")
        except MCPError:
            pass


class TestFromGenericException:
    """Test FastMCPError.from_generic_exception() factory method."""

    def test_value_error_becomes_tool_argument_error(self):
        original = ValueError("invalid value")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, ToolArgumentError)
        assert result.code == -32602
        assert "Invalid params: invalid value" in result.message
        assert result.__cause__ is original

    def test_type_error_becomes_tool_argument_error(self):
        original = TypeError("wrong type")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, ToolArgumentError)
        assert result.code == -32602
        assert "Invalid params: wrong type" in result.message

    def test_permission_error_becomes_authorization_error(self):
        original = PermissionError("denied")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, MCPAuthorizationError)
        assert result.code == -32000
        assert "Permission denied: denied" in result.message

    def test_timeout_error_becomes_timeout_error(self):
        original = TimeoutError("timed out")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, MCPTimeoutError)
        assert result.code == -32000
        assert "Request timeout: timed out" in result.message

    def test_connection_error_becomes_connection_error(self):
        original = ConnectionError("connection failed")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, MCPConnectionError)
        assert result.code == -32000
        assert "Connection error: connection failed" in result.message

    def test_file_not_found_becomes_not_found(self):
        original = FileNotFoundError("file.txt")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, NotFoundError)
        assert result.code == -32001
        assert "Not found: file.txt" in result.message

    def test_key_error_becomes_not_found(self):
        original = KeyError("key")
        result = FastMCPError.from_generic_exception(original)

        assert isinstance(result, NotFoundError)
        assert result.code == -32001

    def test_generic_exception_becomes_fastmcperror(self):
        original = RuntimeError("something went wrong")
        result = FastMCPError.from_generic_exception(original)

        assert type(result) is FastMCPError
        assert result.code == -32603
        assert "Internal error: something went wrong" in result.message

    def test_not_found_with_resource_method(self):
        """Test that method parameter affects NotFoundError code for resources."""
        original = FileNotFoundError("resource.txt")
        result = FastMCPError.from_generic_exception(
            original, method="resources/list"
        )

        assert isinstance(result, NotFoundError)
        assert result.code == -32002
        assert result.resource_type == "resource"
        assert "Resource not found: resource.txt" in result.message

    def test_not_found_with_non_resource_method(self):
        original = FileNotFoundError("something.txt")
        result = FastMCPError.from_generic_exception(
            original, method="tools/list"
        )

        assert isinstance(result, NotFoundError)
        assert result.code == -32001
        assert result.resource_type is None


class TestNotFoundErrorResourceType:
    """Test NotFoundError resource_type parameter."""

    def test_default_no_resource_type(self):
        exc = NotFoundError("test")
        assert exc.resource_type is None
        assert exc.code == -32001
        assert exc.message == "test"

    def test_resource_type_resource(self):
        exc = NotFoundError("my_resource.txt", resource_type="resource")
        assert exc.resource_type == "resource"
        assert exc.code == -32002
        assert exc.message == "my_resource.txt"

    def test_resource_type_tool(self):
        exc = NotFoundError("my_tool", resource_type="tool")
        assert exc.resource_type == "tool"
        assert exc.code == -32001

    def test_resource_type_generic(self):
        exc = NotFoundError("something", resource_type="generic")
        assert exc.resource_type == "generic"
        assert exc.code == -32001

    def test_to_error_data_uses_code(self):
        exc_resource = NotFoundError("test", resource_type="resource")
        exc_generic = NotFoundError("test", resource_type="generic")

        assert exc_resource.to_error_data().code == -32002
        assert exc_generic.to_error_data().code == -32001

    def test_default_message_for_resource_type(self):
        exc = NotFoundError(resource_type="resource")
        assert exc.message == "Resource not found"
        assert exc.code == -32002

    def test_default_message_generic(self):
        exc = NotFoundError()
        assert exc.message == "Not found"
        assert exc.code == -32001


class TestTransportExceptions:
    """Test MCPTransportError and its subclasses."""

    def test_transport_error_base(self):
        exc = MCPTransportError("transport failed")
        assert exc.code == -32000
        assert isinstance(exc, FastMCPError)

    def test_connection_error(self):
        exc = MCPConnectionError("connection refused")
        assert exc.code == -32000
        assert exc.message == "connection refused"
        assert isinstance(exc, MCPTransportError)

    def test_timeout_error(self):
        exc = MCPTimeoutError("operation timed out")
        assert exc.code == -32000
        assert exc.message == "operation timed out"
        assert isinstance(exc, MCPTransportError)

    def test_disconnected_error(self):
        exc = MCPDisconnectedError("connection closed")
        assert exc.code == -32000
        assert exc.message == "connection closed"
        assert isinstance(exc, MCPTransportError)

    def test_default_messages(self):
        """Test default messages when no message is provided."""
        assert MCPConnectionError().message == "Connection error"
        assert MCPTimeoutError().message == "Timeout error"
        assert MCPDisconnectedError().message == "Disconnected"

    def test_all_transport_errors_inherit_mcptransporterror(self):
        transport_subclasses = [
            MCPConnectionError,
            MCPTimeoutError,
            MCPDisconnectedError,
        ]
        for cls in transport_subclasses:
            exc = cls("test")
            assert isinstance(exc, MCPTransportError)


class TestAuthorizationExceptions:
    """Test MCPAuthorizationError and its subclasses."""

    def test_authorization_error_base(self):
        exc = MCPAuthorizationError("auth failed")
        assert exc.code == -32000
        assert isinstance(exc, FastMCPError)

    def test_authorization_error_legacy(self):
        exc = AuthorizationError("access denied")
        assert isinstance(exc, MCPAuthorizationError)
        assert exc.code == -32000


class TestDeprecationWarnings:
    """Test that legacy exceptions emit deprecation warnings.

    Note: These tests require settings.deprecation_warnings to be True.
    The warnings are emitted only when deprecation warnings are enabled.
    """

    def test_tool_error_deprecation_warning(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with pytest.warns(FastMCPDeprecationWarning) as record:
                ToolError("test")

            assert len(record) >= 1
            assert "ToolError is deprecated" in str(record[0].message)
        finally:
            settings.deprecation_warnings = original_value

    def test_resource_error_deprecation_warning(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with pytest.warns(FastMCPDeprecationWarning) as record:
                ResourceError("test")

            assert len(record) >= 1
            assert "ResourceError is deprecated" in str(record[0].message)
        finally:
            settings.deprecation_warnings = original_value

    def test_validation_error_deprecation_warning(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with pytest.warns(FastMCPDeprecationWarning) as record:
                ValidationError("test")

            assert len(record) >= 1
            assert "ValidationError is deprecated" in str(record[0].message)
        finally:
            settings.deprecation_warnings = original_value

    def test_prompt_error_deprecation_warning(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with pytest.warns(FastMCPDeprecationWarning) as record:
                PromptError("test")

            assert len(record) >= 1
            assert "PromptError is deprecated" in str(record[0].message)
        finally:
            settings.deprecation_warnings = original_value

    def test_authorization_error_deprecation_warning(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with pytest.warns(FastMCPDeprecationWarning) as record:
                AuthorizationError("test")

            assert len(record) >= 1
            assert "AuthorizationError is deprecated" in str(record[0].message)
        finally:
            settings.deprecation_warnings = original_value

    def test_subclasses_do_not_emit_warnings(self):
        """ToolNotFoundError, etc. should NOT emit deprecation warnings."""
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = True

        try:
            with warnings.catch_warnings(record=True) as record:
                warnings.simplefilter("always")

                ToolNotFoundError("test")
                ToolArgumentError("test")
                ToolExecutionError("test")
                ResourceNotFoundError("test")
                ResourceAccessDeniedError("test")
                PromptNotFoundError("test")

            fastmcp_deprecations = [
                w for w in record if issubclass(w.category, FastMCPDeprecationWarning)
            ]
            assert len(fastmcp_deprecations) == 0
        finally:
            settings.deprecation_warnings = original_value

    def test_no_warning_when_settings_disabled(self):
        import warnings

        from fastmcp import settings

        original_value = settings.deprecation_warnings
        settings.deprecation_warnings = False

        try:
            with warnings.catch_warnings(record=True) as record:
                warnings.simplefilter("always")
                ToolError("test")
                ResourceError("test")

            fastmcp_deprecations = [
                w for w in record if issubclass(w.category, FastMCPDeprecationWarning)
            ]
            assert len(fastmcp_deprecations) == 0
        finally:
            settings.deprecation_warnings = original_value


class TestProtocolExceptions:
    """Test MCPProtocolError and its subclasses."""

    def test_protocol_error_base(self):
        exc = MCPProtocolError("protocol error")
        assert exc.code == -32600
        assert isinstance(exc, FastMCPError)

    def test_parse_error(self):
        exc = MCPParseError("invalid JSON")
        assert exc.code == -32700
        assert exc.message == "invalid JSON"
        assert isinstance(exc, MCPProtocolError)

    def test_invalid_request_error(self):
        exc = MCPInvalidRequestError("bad request")
        assert exc.code == -32600
        assert exc.message == "bad request"
        assert isinstance(exc, MCPProtocolError)

    def test_method_not_found_error(self):
        exc = MCPMethodNotFoundError("unknown method")
        assert exc.code == -32601
        assert exc.message == "unknown method"
        assert isinstance(exc, MCPProtocolError)

    def test_default_messages(self):
        """Test default messages when no message is provided."""
        assert MCPParseError().message == "Parse error"
        assert MCPInvalidRequestError().message == "Invalid request"
        assert MCPMethodNotFoundError().message == "Method not found"


# Import these at the end for test classes above
from fastmcp.exceptions import (
    FastMCPDeprecationWarning,
    MCPConnectionError,
    MCPDisconnectedError,
    MCPInvalidRequestError,
    MCPMethodNotFoundError,
    MCPParseError,
    MCPTimeoutError,
)

