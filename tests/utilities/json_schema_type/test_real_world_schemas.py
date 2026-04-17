"""Crash-test json_schema_to_type against real-world OpenAPI schemas.

Uses the APIs.guru openapi-directory (https://github.com/APIs-guru/openapi-directory)
pinned to a specific commit for reproducibility.

Parametrized by API provider (~700 providers, one test each) so pytest
shows progress and can identify which provider caused a hang.

Marked as an integration test — skipped by default, run with:

    uv run pytest tests/utilities/json_schema_type/test_real_world_schemas.py -m integration -n auto --timeout-method=thread -q

On a 16-core machine this takes ~3-5 minutes. The CI workflow
(.github/workflows/run-schema-crash-test.yml) runs it on push/PR when
relevant source files change.

## Aggregate baselines

After each run, conftest.py compares aggregate crash counts against baseline
caps in ``conftest.py``.  Ratchet those caps DOWN whenever a cluster of errors
is fixed — never up.  See ``conftest.py`` for the current caps and comments
on what was fixed.

## Collecting and analysing failures

Set ``DUMP_SCHEMA_FAILURES`` to a directory to write per-failure JSONL records:

    RUN_REAL_WORLD_SCHEMA_TEST=1 \\
    OPENAPI_DIRECTORY_PATH=/tmp/openapi-directory \\
    DUMP_SCHEMA_FAILURES=/tmp/schema_failures \\
    uv run pytest tests/utilities/json_schema_type/test_real_world_schemas.py \\
        -m integration -n auto --timeout-method=thread -q

Then cluster the results to find root causes:

    python tests/utilities/json_schema_type/cluster_failures.py

See ``cluster_failures.py`` for the full fix workflow (reproduce → unit test →
fix → ratchet baseline → commit).

## Cloning the corpus locally

The test auto-clones when ``RUN_REAL_WORLD_SCHEMA_TEST=1``.  To pre-clone
manually (~800 MB):

    git clone --depth 1 https://github.com/APIs-guru/openapi-directory.git /tmp/openapi-directory

Then point at it:

    OPENAPI_DIRECTORY_PATH=/tmp/openapi-directory \\
    RUN_REAL_WORLD_SCHEMA_TEST=1 \\
    uv run pytest ...
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter
from yaml import CSafeLoader  # ty: ignore[possibly-missing-import]

from fastmcp.utilities.json_schema_type import json_schema_to_type

# Pin to a specific commit for reproducibility
OPENAPI_DIRECTORY_REPO = "https://github.com/APIs-guru/openapi-directory.git"
OPENAPI_DIRECTORY_COMMIT = "f7207cf0a5c56081d275ebae4cf615249323385d"
CLONE_DIR = Path(os.environ.get("OPENAPI_DIRECTORY_PATH", "/tmp/openapi-directory"))

# Per-schema timeout (seconds) to catch infinite loops
SCHEMA_TIMEOUT = 5

# In CI (RUN_REAL_WORLD_SCHEMA_TEST=1), _ensure_repo clones automatically.
# Locally, skip unless the repo is already cloned to avoid a surprise 200MB download.
_run_in_ci = os.environ.get("RUN_REAL_WORLD_SCHEMA_TEST") == "1"
_skip_locally = not _run_in_ci and not CLONE_DIR.exists()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _skip_locally,
        reason=(
            f"openapi-directory not found at {CLONE_DIR}. "
            f"Set RUN_REAL_WORLD_SCHEMA_TEST=1 to auto-clone, or: "
            f"git clone --depth 1 {OPENAPI_DIRECTORY_REPO} {CLONE_DIR}"
        ),
    ),
]


class _SchemaTimeout(Exception):
    pass


def _alarm_handler(signum: object, frame: object) -> None:
    raise _SchemaTimeout()


# ── Helpers ──────────────────────────────────────────────────────────


def _is_openapi_directory_clone(path: Path) -> bool:
    """Check whether *path* looks like a clone of the openapi-directory repo."""
    if not (path / ".git").is_dir():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    return "openapi-directory" in result.stdout


def _ensure_repo() -> Path:
    """Clone the openapi-directory repo if not already present at the pinned commit.

    Safe under pytest-xdist: a file lock serializes the rmtree/reclone path so
    concurrent workers don't race on the shared CLONE_DIR.
    """
    # Fast-path check without a lock — if HEAD already matches, skip locking entirely.
    if _head_matches_pinned():
        return CLONE_DIR

    # fcntl is POSIX-only; imported here (not at module level) so this file
    # collects cleanly on Windows where the integration test is skipped.
    import fcntl

    lock_path = CLONE_DIR.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            # Re-check under the lock in case another worker already fixed it.
            if _head_matches_pinned():
                return CLONE_DIR

            if CLONE_DIR.exists():
                if not _is_openapi_directory_clone(CLONE_DIR):
                    raise RuntimeError(
                        f"{CLONE_DIR} exists but is not an openapi-directory clone. "
                        f"Remove it manually or set OPENAPI_DIRECTORY_PATH to a different path."
                    )
                shutil.rmtree(CLONE_DIR)

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    OPENAPI_DIRECTORY_REPO,
                    str(CLONE_DIR),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(CLONE_DIR),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    OPENAPI_DIRECTORY_COMMIT,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(CLONE_DIR), "checkout", OPENAPI_DIRECTORY_COMMIT],
                check=True,
                capture_output=True,
            )
            return CLONE_DIR
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _head_matches_pinned() -> bool:
    """Return True when CLONE_DIR is already checked out at the pinned commit."""
    if not (CLONE_DIR.exists() and (CLONE_DIR / ".git").is_dir()):
        return False
    result = subprocess.run(
        ["git", "-C", str(CLONE_DIR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == OPENAPI_DIRECTORY_COMMIT


def _load_spec(spec_file: Path) -> dict | None:
    """Load and return a spec dict, or None on failure."""
    try:
        if spec_file.suffix == ".yaml":
            spec = yaml.load(spec_file.read_text(), Loader=CSafeLoader)
        else:
            spec = json.loads(spec_file.read_text())
        return spec if isinstance(spec, dict) else None
    except Exception:
        return None


def _extract_schemas(spec: dict) -> dict:
    """Pull all schema definitions out of an OpenAPI spec."""
    schemas: dict = {}
    if "definitions" in spec:
        schemas.update(spec["definitions"])
    components = spec.get("components")
    if isinstance(components, dict):
        schemas.update(components.get("schemas", {}))
    return {k: v for k, v in schemas.items() if isinstance(v, dict)}


# ── Per-provider collection ──────────────────────────────────────────


def _collect_providers() -> list[str]:
    """List API provider directories (e.g. 'github.com', 'amazonaws.com')."""
    apis_dir = CLONE_DIR / "APIs"
    if not apis_dir.is_dir():
        return []
    return sorted(d.name for d in apis_dir.iterdir() if d.is_dir())


def _spec_files_for_provider(provider: str) -> list[Path]:
    """Find all spec files for a given provider."""
    provider_dir = CLONE_DIR / "APIs" / provider
    files: list[Path] = []
    for name in ("openapi.yaml", "swagger.yaml", "openapi.json", "swagger.json"):
        files.extend(provider_dir.rglob(name))
    return sorted(files)


# ── Test logic ───────────────────────────────────────────────────────


@dataclass
class ProviderResult:
    """Crash counts for one API provider."""

    schemas: int = 0
    type_errors: int = 0
    schema_errors: int = 0
    timeouts: int = 0
    other_errors: int = 0


def _test_provider(provider: str) -> ProviderResult:
    """Run json_schema_to_type on every schema for one provider."""
    # Clear the module-level type cache between providers to avoid
    # unbounded memory growth across 232K schemas.
    from fastmcp.utilities.json_schema_type import _classes

    _classes.clear()

    result = ProviderResult()
    use_alarm = hasattr(signal, "SIGALRM")

    # Optional per-failure dump: when DUMP_SCHEMA_FAILURES is set to a directory,
    # write one JSONL record per failure so we can cluster them later.
    dump_dir_env = os.environ.get("DUMP_SCHEMA_FAILURES")
    dump_path: Path | None = None
    if dump_dir_env:
        dump_path = Path(dump_dir_env) / f"{provider}.jsonl"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate any prior content from a previous run of this provider.
        dump_path.write_text("")

    def _record_failure(
        bucket: str, schema_name: str, schema_obj: dict, exc: BaseException
    ) -> None:
        if dump_path is None:
            return
        # Cap the schema snippet so huge recursive specs don't blow up disk.
        try:
            schema_repr = json.dumps(schema_obj, default=str)[:2000]
        except Exception:
            schema_repr = "<unserializable>"
        record = {
            "provider": provider,
            "name": schema_name,
            "bucket": bucket,
            "error_type": type(exc).__name__,
            "error_msg": str(exc)[:500],
            "schema": schema_repr,
        }
        with dump_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    for spec_file in _spec_files_for_provider(provider):
        spec = _load_spec(spec_file)
        if spec is None:
            continue
        for _name, schema in _extract_schemas(spec).items():
            # JSON-round-trip to simulate production: schemas arrive over
            # MCP as JSON, so YAML-specific types (datetime, date) should
            # not be present.  This avoids counting YAML-parser artifacts
            # as json_schema_to_type bugs.
            try:
                schema = json.loads(json.dumps(schema, default=str))
            except (TypeError, ValueError):
                continue

            result.schemas += 1

            old_handler = signal.SIG_DFL
            if use_alarm:
                old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
                signal.alarm(SCHEMA_TIMEOUT)
            try:
                T = json_schema_to_type(schema)
                TypeAdapter(T)
            except _SchemaTimeout as e:
                result.timeouts += 1
                _record_failure("timeouts", _name, schema, e)
            except TypeError as e:
                result.type_errors += 1
                _record_failure("type_errors", _name, schema, e)
            except Exception as e:
                err_type = type(e).__name__
                if "SchemaError" in err_type or "schema" in str(e).lower()[:50]:
                    result.schema_errors += 1
                    _record_failure("schema_errors", _name, schema, e)
                else:
                    result.other_errors += 1
                    _record_failure("other_errors", _name, schema, e)
            finally:
                if use_alarm:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)

    return result


# ── Per-provider test (parametrized) ─────────────────────────────────
#
# ~700 test items — one per API provider.
# Profiled on a fast MacBook (p50=0.06s, p99=8s); CI is ~3x slower.
# Tiered timeouts so small providers fail fast while large ones get room.
#
# Local times → CI estimate → timeout bucket:
#   azure/aws/github/msft/google  80-137s → 240-410s → 600s
#   adyen                         21s     → 63s      → 120s
#   loket/mailchimp/apisetu/k8s   6-10s   → 18-30s   → 120s
#   everything else (p99)         <8s     → <24s     → 60s

_TIER1_PROVIDERS = frozenset(
    {
        "azure.com",
        "amazonaws.com",
        "googleapis.com",
        "github.com",
        "microsoft.com",
    }
)

_TIER2_PROVIDERS = frozenset(
    {
        "adyen.com",
        "loket.nl",
        "mailchimp.com",
        "apisetu.gov.in",
        "kubernetes.io",
        "twilio.com",
        "sportsdata.io",
        "vtex.local",
        "amadeus.com",
    }
)


def _providers_with_timeouts() -> list:  # list of pytest.param
    """Build parametrize list with per-provider timeouts."""
    params = []
    for p in _collect_providers():
        if p in _TIER1_PROVIDERS:
            t = 600
        elif p in _TIER2_PROVIDERS:
            t = 120
        else:
            t = 60
        params.append(
            pytest.param(p, id=p, marks=pytest.mark.timeout(t, method="thread"))
        )
    return params


# Per-provider results are persisted to disk so they survive across xdist
# workers (each worker runs in its own process). Aggregation and baseline
# assertions happen in the pytest_sessionfinish hook in conftest.py.
# This path is duplicated in conftest.py by design — tests shouldn't import
# from conftest, which isn't a reliably importable module.
_RESULTS_DIR = Path(
    os.environ.get("SCHEMA_CRASH_RESULTS_DIR", "/tmp/schema_crash_results")
)


@pytest.mark.integration
@pytest.mark.parametrize("provider", _providers_with_timeouts())
def test_provider_schemas(provider: str):
    """json_schema_to_type should not infinite-loop on schemas from this provider."""
    _ensure_repo()
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result = _test_provider(provider)
    (_RESULTS_DIR / f"{provider}.json").write_text(json.dumps(asdict(result)))
    assert result.timeouts == 0, (
        f"{provider}: {result.timeouts} schema(s) timed out (possible infinite loop)"
    )
