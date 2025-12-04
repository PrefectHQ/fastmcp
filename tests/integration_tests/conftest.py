import os

import pytest


def _is_rate_limit_error(excinfo) -> bool:
    """Check if an exception indicates a rate limit error from GitHub API."""
    if excinfo is None:
        return False

    exc = excinfo.value
    exc_type = excinfo.typename
    exc_str = str(exc).lower()

    # BrokenResourceError typically indicates connection closed due to rate limit
    if exc_type == "BrokenResourceError":
        return True

    # httpx.HTTPStatusError with 429 status
    if exc_type == "HTTPStatusError":
        try:
            if hasattr(exc, "response") and exc.response.status_code == 429:
                return True
        except Exception:
            pass

    # Check for rate limit indicators in exception message
    if "429" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
        return True

    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert rate limit failures to skips for GitHub integration tests."""
    outcome = yield
    report = outcome.get_result()

    # Only process actual failures during the call phase, not xfails
    if (
        report.when == "call"
        and report.failed
        and not hasattr(report, "wasxfail")
        and item.module.__name__ == "tests.integration_tests.test_github_mcp_remote"
        and _is_rate_limit_error(call.excinfo)
    ):
        report.outcome = "skipped"
        report.longrepr = (
            os.path.abspath(__file__),
            None,
            "Skipped: Skipping due to GitHub API rate limit (429)",
        )
