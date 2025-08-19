"""Tests for deprecated dependencies parameter.

This entire file can be deleted when the dependencies parameter is removed (deprecated in v2.11.4).
"""

import warnings

import pytest

from fastmcp import FastMCP


def test_dependencies_parameter_deprecated():
    """Test that using the dependencies parameter raises a deprecation warning."""

    with pytest.warns(DeprecationWarning, match="deprecated as of FastMCP 2.11.4"):
        server = FastMCP("Test Server", dependencies=["pandas", "numpy"])

    # Should still work for backward compatibility
    assert server.dependencies == ["pandas", "numpy"]


def test_no_warning_without_dependencies():
    """Test that no warning is raised when dependencies are not used."""

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # Turn warnings into errors
        server = FastMCP("Test Server")  # Should not raise

    assert server.dependencies == []  # Should use default empty list
