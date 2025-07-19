import pytest


def pytest_collection_modifyitems(items):
    """Automatically mark tests in integration_tests folder with 'integration' marker."""
    for item in items:
        # Check if the test is in the integration_tests folder
        if "integration_tests" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
