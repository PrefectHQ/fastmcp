import warnings

import pytest
from starlette.applications import Starlette

from fastmcp import FastMCP
from fastmcp.utilities.tests import temporary_settings

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestDeprecationWarningsSetting:
    def test_deprecation_warnings_setting_true(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning) as recorded_warnings:
                # will warn once for providing deprecated arg
                mcp = FastMCP(host="1.2.3.4")
                # will warn once for accessing deprecated property
                mcp.settings

            assert len(recorded_warnings) == 2

    def test_deprecation_warnings_setting_false(self):
        with temporary_settings(deprecation_warnings=False):
            # will error if a warning is raised
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                # will warn once for providing deprecated arg
                mcp = FastMCP(host="1.2.3.4")
                # will warn once for accessing deprecated property
                mcp.settings


def test_http_app_with_sse_transport():
    """Test that http_app with SSE transport works (no warning)."""
    server = FastMCP("TestServer")

    # This should not raise a warning since we're using the new API
    with warnings.catch_warnings(record=True) as recorded_warnings:
        app = server.http_app(transport="sse")
        assert isinstance(app, Starlette)

        # Verify no deprecation warnings were raised for using transport parameter
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0
