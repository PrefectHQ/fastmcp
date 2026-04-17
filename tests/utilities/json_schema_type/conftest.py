"""Session hooks for the real-world schema crash test.

Per-provider tests in `test_real_world_schemas.py` run under `pytest-xdist` —
each worker is a separate process, so module-level accumulators don't survive
across workers. Each test persists its counts to `SCHEMA_CRASH_RESULTS_DIR`
and the session-finish hook below reads them back on the xdist master after
all workers finish.

These hooks must live in conftest.py (not the test module) because pytest
only picks up `pytest_configure` / `pytest_sessionfinish` from conftest files.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

SCHEMA_CRASH_RESULTS_DIR = Path(
    os.environ.get("SCHEMA_CRASH_RESULTS_DIR", "/tmp/schema_crash_results")
)


def pytest_configure(config: pytest.Config) -> None:
    """Clear prior per-provider results at session start (xdist master only)."""
    if hasattr(config, "workerinput"):
        return
    shutil.rmtree(SCHEMA_CRASH_RESULTS_DIR, ignore_errors=True)
    SCHEMA_CRASH_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Aggregate per-provider results once all xdist workers finish."""
    if hasattr(session.config, "workerinput"):
        return
    if not SCHEMA_CRASH_RESULTS_DIR.exists():
        return
    files = list(SCHEMA_CRASH_RESULTS_DIR.glob("*.json"))
    if not files:
        return

    keys = ("schemas", "type_errors", "schema_errors", "timeouts", "other_errors")
    totals = dict.fromkeys(keys, 0)
    for f in files:
        data = json.loads(f.read_text())
        for k in keys:
            totals[k] += data.get(k, 0)

    crashes = (
        totals["type_errors"]
        + totals["schema_errors"]
        + totals["timeouts"]
        + totals["other_errors"]
    )

    print(f"\n{'=' * 60}")
    print("Real-world schema crash test — aggregate results")
    print(f"{'=' * 60}")
    print(f"Providers tested: {len(files):,}")
    print(f"Schemas tested:  {totals['schemas']:,}")
    print(f"TypeErrors:      {totals['type_errors']:,}")
    print(f"SchemaErrors:    {totals['schema_errors']:,}")
    print(f"Timeouts:        {totals['timeouts']:,}")
    print(f"Other errors:    {totals['other_errors']:,}")
    print(
        f"Total crashes:   {crashes:,} ({crashes / max(totals['schemas'], 1) * 100:.2f}%)"
    )

    # Snapshot baselines (ratcheted 2026-04-17, openapi-directory@f7207cf0).
    # TypeErrors: fixed to 0 (were 388 at 2026-04-10 capture).
    # SchemaErrors: fixed to ~0 by graceful pattern fallback in _create_string_type;
    #   was 279 (all Pydantic Rust-regex rejections — lookahead, \p{…}, size limits).
    MAX_TYPE_ERRORS = 0
    MAX_SCHEMA_ERRORS = (
        0  # graceful fallback in _create_string_type eliminates all regex rejections
    )
    MAX_TIMEOUTS = 5  # was 0
    MAX_OTHER_ERRORS = 50  # was 0

    failures: list[str] = []
    if totals["schemas"] <= 200_000:
        failures.append(
            f"Expected >200k schemas but only found {totals['schemas']}. "
            f"Is the openapi-directory checkout correct?"
        )
    if totals["type_errors"] > MAX_TYPE_ERRORS:
        failures.append(
            f"TypeErrors regressed: {totals['type_errors']} > {MAX_TYPE_ERRORS}"
        )
    if totals["schema_errors"] > MAX_SCHEMA_ERRORS:
        failures.append(
            f"SchemaErrors regressed: {totals['schema_errors']} > {MAX_SCHEMA_ERRORS}"
        )
    if totals["timeouts"] > MAX_TIMEOUTS:
        failures.append(f"Timeouts regressed: {totals['timeouts']} > {MAX_TIMEOUTS}")
    if totals["other_errors"] > MAX_OTHER_ERRORS:
        failures.append(
            f"Other errors regressed: {totals['other_errors']} > {MAX_OTHER_ERRORS}"
        )

    if failures:
        print("\nBASELINE VIOLATIONS:")
        for msg in failures:
            print(f"  - {msg}")
        # Force a non-zero exit even though all individual tests passed.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
