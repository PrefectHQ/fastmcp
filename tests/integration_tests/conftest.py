import os

import pytest


def _is_rate_limit_error(excinfo, report=None) -> bool:
    """Check if an exception indicates a rate limit error from GitHub API.

    Args:
        excinfo: The exception info from pytest
        report: Optional test report for additional context (captured output, longrepr)
    """
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

    # Timeout exceptions may indicate rate limiting when the 429 causes asyncio
    # shutdown issues. Check if it's a timeout and look for 429 in the captured output.
    if "timeout" in exc_type.lower() or "timeout" in exc_str:
        # Check captured output for 429 indicators
        if report is not None:
            longrepr_str = str(report.longrepr).lower() if report.longrepr else ""
            if "429" in longrepr_str or "too many requests" in longrepr_str:
                return True

            # Check captured stdout/stderr
            for section_name, content in getattr(report, "sections", []):
                if "429" in content or "too many requests" in content.lower():
                    return True

    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert rate limit failures to skips for GitHub integration tests."""
    outcome = yield
    report = outcome.get_result()

    # Only process actual failures during the call or teardown phase, not xfails
    if (
        report.when in ("call", "teardown")
        and report.failed
        and not hasattr(report, "wasxfail")
        and item.module.__name__ == "tests.integration_tests.test_github_mcp_remote"
        and _is_rate_limit_error(call.excinfo, report)
    ):
        report.outcome = "skipped"
        report.longrepr = (
            os.path.abspath(__file__),
            None,
            "Skipped: Skipping due to GitHub API rate limit (429)",
        )
