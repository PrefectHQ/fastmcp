import hashlib

import pytest

from flows.agentic_reproduction_evidence import materialize_regression


def test_exact_regression_patch_materializes_on_recorded_base(repository):
    directory, spec = repository
    patch = spec.layers[0].patch
    proof = materialize_regression(
        directory, {"base_sha": spec.trunk_sha, "regression_patch": patch}
    )
    assert proof["status"] == "materialized"
    assert proof["patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert proof["paths"] == ["tests/first.txt"]


def test_truncated_patch_is_not_accepted_as_reusable_evidence(repository):
    directory, spec = repository
    with pytest.raises(RuntimeError, match="corrupt patch"):
        materialize_regression(
            directory,
            {
                "base_sha": spec.trunk_sha,
                "regression_patch": spec.layers[0].patch.rstrip("\n"),
            },
        )


def test_reproduction_cannot_modify_library_code(repository):
    directory, spec = repository
    with pytest.raises(ValueError, match="tests only"):
        materialize_regression(
            directory,
            {
                "base_sha": spec.trunk_sha,
                "regression_patch": spec.layers[0].patch.replace(
                    "tests/", "fastmcp_slim/"
                ),
            },
        )
