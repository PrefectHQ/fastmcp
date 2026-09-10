from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from fastmcp.skills.models import (
    ListSkillsResult,
    Skill,
    SkillFrontmatter,
    SkillResource,
)

DIGEST = f"sha256:{'a' * 64}"


def make_skill(**overrides: object) -> Skill:
    values: dict[str, object] = {
        "uri": "skill://acme/review/SKILL.md",
        "frontmatter": {
            "name": "review",
            "description": "Review a change",
            "metadata": {"author": "acme"},
            "future": {"modes": ["quick", "deep"], "enabled": True},
        },
        "resources": [
            {
                "uri": "skill://acme/review/SKILL.md",
                "digest": DIGEST,
                "size": 42,
            }
        ],
    }
    values.update(overrides)
    return Skill.model_validate(values)


def test_frontmatter_preserves_unknown_json_fields() -> None:
    skill = make_skill()

    assert skill.frontmatter.model_dump() == {
        "name": "review",
        "description": "Review a change",
        "metadata": {"author": "acme"},
        "future": {"modes": ["quick", "deep"], "enabled": True},
    }


def test_frontmatter_preserves_allowed_tools_wire_name() -> None:
    frontmatter = SkillFrontmatter(
        {
            "name": "review",
            "description": "Review a change",
            "allowed-tools": "Read Grep",
        }
    )

    assert frontmatter.model_dump()["allowed-tools"] == "Read Grep"


@pytest.mark.parametrize("name", ["Review", "-review", "review-", "deep--review"])
def test_frontmatter_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError, match="name must be"):
        SkillFrontmatter({"name": name, "description": "Review a change"})


def test_frontmatter_rejects_whitespace_description() -> None:
    with pytest.raises(ValidationError, match="description must contain"):
        SkillFrontmatter({"name": "review", "description": "   "})


def test_frontmatter_rejects_non_json_extra_values() -> None:
    with pytest.raises(ValidationError):
        SkillFrontmatter.model_validate(
            {"name": "review", "description": "Review a change", "future": object()}
        )


def test_frontmatter_rejects_non_finite_extra_values() -> None:
    with pytest.raises(ValidationError):
        SkillFrontmatter(
            {"name": "review", "description": "Review a change", "future": math.inf}
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("license", None, "license must be a string"),
        ("compatibility", None, "compatibility must be a string"),
        ("metadata", None, "metadata must map"),
        ("metadata", {"version": 1}, "metadata must map"),
        ("allowed-tools", None, "allowed-tools must be a string"),
    ],
)
def test_frontmatter_rejects_invalid_known_optional_fields(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        SkillFrontmatter.model_validate(
            {"name": "review", "description": "Review a change", field: value}
        )


def test_frontmatter_dump_does_not_add_absent_optional_fields() -> None:
    frontmatter = SkillFrontmatter({"name": "review", "description": "Review a change"})

    assert frontmatter.model_dump() == {
        "name": "review",
        "description": "Review a change",
    }


def test_skill_accepts_dynamic_resources() -> None:
    skill = make_skill(resources="dynamic")

    assert skill.resources == "dynamic"


def test_skill_requires_uri_name_to_match_frontmatter() -> None:
    with pytest.raises(ValidationError, match="must equal frontmatter.name"):
        make_skill(uri="skill://acme/other/SKILL.md")


def test_skill_accepts_non_skill_scheme_and_nested_path() -> None:
    uri = "github://acme/repository/skills/review/SKILL.md"
    skill = make_skill(
        uri=uri,
        resources=[
            {"uri": uri, "digest": DIGEST, "size": 42},
            {
                "uri": "github://acme/repository/skills/review/examples/good.md",
                "digest": DIGEST,
                "size": 8,
            },
        ],
    )

    assert skill.uri == uri


def test_static_manifest_requires_skill_md() -> None:
    with pytest.raises(ValidationError, match="must include"):
        make_skill(
            resources=[
                {
                    "uri": "skill://acme/review/reference.md",
                    "digest": DIGEST,
                    "size": 42,
                }
            ]
        )


def test_static_manifest_rejects_duplicate_uris() -> None:
    resource = SkillResource(uri="skill://acme/review/SKILL.md", digest=DIGEST, size=42)
    with pytest.raises(ValidationError, match="exactly once"):
        make_skill(resources=[resource, resource])


@pytest.mark.parametrize(
    "uri",
    [
        "skill://other/reference.md",
        "https://acme/review/reference.md",
        "skill://acme/outside/reference.md",
        "skill://acme/review/../outside.md",
        "skill://acme/review/%2e%2e/outside.md",
    ],
)
def test_static_manifest_rejects_resources_outside_skill(uri: str) -> None:
    with pytest.raises(
        ValidationError, match="within the skill directory|invalid segment"
    ):
        make_skill(
            resources=[
                {
                    "uri": "skill://acme/review/SKILL.md",
                    "digest": DIGEST,
                    "size": 42,
                },
                {"uri": uri, "digest": DIGEST, "size": 8},
            ]
        )


@pytest.mark.parametrize(
    "digest",
    [
        "sha256:abc",
        f"sha256:{'A' * 64}",
        f"sha512:{'a' * 64}",
    ],
)
def test_resource_requires_canonical_sha256(digest: str) -> None:
    with pytest.raises(ValidationError):
        SkillResource(uri="skill://review/SKILL.md", digest=digest, size=1)


def test_resource_rejects_negative_size() -> None:
    with pytest.raises(ValidationError):
        SkillResource(uri="skill://review/SKILL.md", digest=DIGEST, size=-1)


def test_list_result_uses_protocol_wire_names() -> None:
    wire = ListSkillsResult(skills=[make_skill()]).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )

    assert wire["resultType"] == "complete"
    assert wire["ttlMs"] == 0
    assert wire["cacheScope"] == "private"
