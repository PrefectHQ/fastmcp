"""Test experimental OpenAPI parser feature flag behavior."""

import httpx
import pytest
from fastapi import FastAPI

from fastmcp import FastMCP
from fastmcp.experimental.server.openapi import (
    FastMCPOpenAPI as ExperimentalFastMCPOpenAPI,
)
from fastmcp.server.openapi import FastMCPOpenAPI as LegacyFastMCPOpenAPI
from fastmcp.utilities.tests import temporary_settings


class TestOpenAPIExperimentalFeatureFlag:
    """Test experimental OpenAPI parser feature flag behavior."""

    @pytest.fixture
    def simple_openapi_spec(self):
        """Simple OpenAPI spec for testing."""
        return {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "test_operation",
                        "summary": "Test operation",
                        "responses": {"200": {"description": "Success"}},
                    }
                }
            },
        }

    @pytest.fixture
    def mock_client(self):
        """Mock HTTP client."""
        return httpx.AsyncClient(base_url="https://api.example.com")

    def test_from_openapi_uses_legacy_by_default(
        self, simple_openapi_spec, mock_client
    ):
        """Test that from_openapi uses legacy parser by default."""
        # Create server using from_openapi (should use legacy by default)
        server = FastMCP.from_openapi(
            openapi_spec=simple_openapi_spec, client=mock_client
        )

        # Should be the legacy implementation
        assert isinstance(server, LegacyFastMCPOpenAPI)
        # Note: Log message "Using legacy OpenAPI parser..." is emitted during creation

    def test_from_openapi_uses_experimental_with_flag(
        self, simple_openapi_spec, mock_client
    ):
        """Test that from_openapi uses experimental parser with flag enabled."""
        # Create server with experimental flag enabled
        with temporary_settings(experimental__enable_new_openapi_parser=True):
            server = FastMCP.from_openapi(
                openapi_spec=simple_openapi_spec, client=mock_client
            )

        # Should be the experimental implementation
        assert isinstance(server, ExperimentalFastMCPOpenAPI)
        # Note: No log message should be emitted when using experimental parser

    def test_from_fastapi_uses_legacy_by_default(self):
        """Test that from_fastapi uses legacy parser by default."""
        # Create a simple FastAPI app
        app = FastAPI(title="Test API")

        @app.get("/test")
        def test_endpoint():
            return {"message": "test"}

        # Create server using from_fastapi (should use legacy by default)
        server = FastMCP.from_fastapi(app=app)

        # Should be the legacy implementation
        assert isinstance(server, LegacyFastMCPOpenAPI)
        # Note: Log message "Using legacy OpenAPI parser..." is emitted during creation

    def test_from_fastapi_uses_experimental_with_flag(self):
        """Test that from_fastapi uses experimental parser with flag enabled."""
        # Create a simple FastAPI app
        app = FastAPI(title="Test API")

        @app.get("/test")
        def test_endpoint():
            return {"message": "test"}

        # Create server with experimental flag enabled
        with temporary_settings(experimental__enable_new_openapi_parser=True):
            server = FastMCP.from_fastapi(app=app)

        # Should be the experimental implementation
        assert isinstance(server, ExperimentalFastMCPOpenAPI)
        # Note: No log message should be emitted when using experimental parser
