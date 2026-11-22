from __future__ import annotations

import pytest

from fastmcp.skills._snapshot import parse_frontmatter


@pytest.mark.parametrize(
    "extra",
    [
        "name: review",
        "custom: {a: 1, a: 2}",
        "custom: {1: value}",
        "custom: 2026-10-12",
        "custom: .nan",
        "custom: .inf",
        "custom: !!binary SGVsbG8=",
        "custom: !!set {a: null}",
        "custom: &cycle [*cycle]",
        "custom: {a: &x 1, b: *x}",
        "metadata: [author, version]",
    ],
)
def test_rejects_lossy_or_invalid_frontmatter(extra: str):
    data = f"---\nname: review\ndescription: Review code\n{extra}\n---\n".encode()
    with pytest.raises(ValueError):
        parse_frontmatter(data)
