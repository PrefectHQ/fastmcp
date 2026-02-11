"""Tests for OpenAPI-related deprecations in 2.14."""

import importlib
import warnings

import pytest

pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestExperimentalOpenAPIImportDeprecation:
    """Test experimental OpenAPI import path deprecations."""

    def test_experimental_server_openapi_import_warns(self):
        """Importing from fastmcp.experimental.server.openapi should warn."""
        import fastmcp.experimental.server.openapi

        with pytest.warns(
            DeprecationWarning,
            match=r"Importing from fastmcp\.experimental\.server\.openapi is deprecated",
        ):
            importlib.reload(fastmcp.experimental.server.openapi)

    def test_experimental_utilities_openapi_import_warns(self):
        """Importing from fastmcp.experimental.utilities.openapi should warn."""
        import fastmcp.experimental.utilities.openapi

        with pytest.warns(
            DeprecationWarning,
            match=r"Importing from fastmcp\.experimental\.utilities\.openapi is deprecated",
        ):
            importlib.reload(fastmcp.experimental.utilities.openapi)

    def test_experimental_imports_resolve_to_same_classes(self):
        """Experimental imports should resolve to the same classes as main imports."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            from fastmcp.experimental.server.openapi import (
                FastMCPOpenAPI as ExpFastMCPOpenAPI,
            )
            from fastmcp.experimental.server.openapi import MCPType as ExpMCPType
            from fastmcp.experimental.server.openapi import RouteMap as ExpRouteMap
            from fastmcp.experimental.utilities.openapi import (
                HTTPRoute as ExpHTTPRoute,
            )
            from fastmcp.server.openapi import FastMCPOpenAPI, MCPType, RouteMap
            from fastmcp.utilities.openapi import HTTPRoute

        assert FastMCPOpenAPI is ExpFastMCPOpenAPI
        assert RouteMap is ExpRouteMap
        assert MCPType is ExpMCPType
        assert HTTPRoute is ExpHTTPRoute
