"""Tests for deprecated OpenAPI imports.

These tests verify that the old import paths still work and emit
deprecation warnings, ensuring backwards compatibility.
"""

import warnings

import httpx


class TestDeprecatedServerOpenAPIImports:
    """Test deprecated imports from fastmcp.server.openapi."""

    def test_import_fastmcp_openapi_emits_warning(self):
        """Importing from fastmcp.server.openapi should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Force reimport
            import importlib

            import fastmcp.server.openapi

            importlib.reload(fastmcp.server.openapi)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1
            assert "providers.openapi" in str(deprecation_warnings[0].message)

    def test_import_routing_emits_warning(self):
        """Importing from fastmcp.server.openapi.routing should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import importlib

            import fastmcp.server.openapi.routing

            importlib.reload(fastmcp.server.openapi.routing)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1
            assert "providers.openapi" in str(deprecation_warnings[0].message)

    def test_fastmcp_openapi_class_emits_warning(self):
        """Using FastMCPOpenAPI should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from fastmcp.server.openapi.server import FastMCPOpenAPI

            spec = {
                "openapi": "3.0.0",
                "info": {"title": "Test", "version": "1.0.0"},
                "paths": {},
            }
            client = httpx.AsyncClient(base_url="https://example.com")
            FastMCPOpenAPI(openapi_spec=spec, client=client)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1
            assert "FastMCPOpenAPI" in str(deprecation_warnings[-1].message)

    def test_deprecated_imports_still_work(self):
        """All expected symbols should be importable from deprecated locations."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            from fastmcp.server.openapi import (
                FastMCPOpenAPI,
                MCPType,
                OpenAPIProvider,
                RouteMap,
            )

            # Verify they're the right types
            assert FastMCPOpenAPI is not None
            assert OpenAPIProvider is not None
            assert MCPType.TOOL.value == "TOOL"
            assert RouteMap is not None

    def test_deprecated_routing_imports_still_work(self):
        """Routing symbols should be importable from deprecated location."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            from fastmcp.server.openapi.routing import (
                DEFAULT_ROUTE_MAPPINGS,
                MCPType,
                _determine_route_type,
            )

            assert DEFAULT_ROUTE_MAPPINGS is not None
            assert len(DEFAULT_ROUTE_MAPPINGS) > 0
            assert MCPType.TOOL.value == "TOOL"
            assert _determine_route_type is not None


class TestDeprecatedExperimentalOpenAPIImports:
    """Test deprecated imports from fastmcp.experimental.server.openapi."""

    def test_experimental_import_emits_warning(self):
        """Importing from experimental should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import importlib

            import fastmcp.experimental.server.openapi

            importlib.reload(fastmcp.experimental.server.openapi)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1
            assert "providers.openapi" in str(deprecation_warnings[0].message)

    def test_experimental_imports_still_work(self):
        """All expected symbols should be importable from experimental."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            from fastmcp.experimental.server.openapi import (
                DEFAULT_ROUTE_MAPPINGS,
                FastMCPOpenAPI,
                MCPType,
            )

            assert FastMCPOpenAPI is not None
            assert DEFAULT_ROUTE_MAPPINGS is not None
            assert MCPType.TOOL.value == "TOOL"


class TestDeprecatedComponentsImports:
    """Test deprecated imports from fastmcp.server.openapi.components."""

    def test_components_import_emits_warning(self):
        """Importing from components should emit deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import importlib

            import fastmcp.server.openapi.components

            importlib.reload(fastmcp.server.openapi.components)

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) >= 1
            assert "providers.openapi" in str(deprecation_warnings[0].message)

    def test_components_imports_still_work(self):
        """Component classes should be importable from deprecated location."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            from fastmcp.server.openapi.components import (
                OpenAPIResource,
                OpenAPIResourceTemplate,
                OpenAPITool,
            )

            assert OpenAPITool is not None
            assert OpenAPIResource is not None
            assert OpenAPIResourceTemplate is not None
