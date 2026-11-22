from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from flows.agentic_stack import git
from flows.agentic_validate_patch import apply_patch, fetch_source


def validate_reproduction_patch(outcome: dict) -> dict:
    patch = outcome.get("regression_patch", "")
    if not patch:
        return {"status": "probe_only", "reason": "No serialized test patch supplied"}
    with tempfile.TemporaryDirectory(
        prefix="fastmcp-regression-evidence-"
    ) as temporary:
        repository = Path(temporary) / "source"
        fetch_source.fn(repository, outcome["base_sha"])
        return materialize_regression(repository, outcome)


def materialize_regression(repository: Path, outcome: dict) -> dict:
    patch = outcome["regression_patch"]
    if git(repository, "rev-parse", "HEAD") != outcome["base_sha"]:
        raise ValueError("Regression checkout differs from its reported baseline")
    paths = apply_patch(repository, patch)
    if any(not path.startswith("tests/") for path in paths):
        raise ValueError("Reproduction evidence must change tests only")
    return {
        "status": "materialized",
        "base_sha": outcome["base_sha"],
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "paths": paths,
    }
