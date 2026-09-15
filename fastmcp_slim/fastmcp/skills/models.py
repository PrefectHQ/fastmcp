"""Wire models for the MCP Skills extension (SEP-2640)."""

from __future__ import annotations

import math
import re
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

import mcp_types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    field_validator,
    model_validator,
)

_SKILL_NAME_PATTERN = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]+(?<!-)$")
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


def _parse_resource_uri(uri: str) -> tuple[str, str, tuple[str, ...]]:
    parsed = urlsplit(uri)
    if not parsed.scheme:
        raise ValueError("resource URI must include a scheme")

    segments: list[str] = []
    for raw_segment in parsed.path.split("/"):
        if not raw_segment:
            continue
        segment = unquote(raw_segment)
        if segment in {".", ".."} or "/" in segment or "\\" in segment:
            raise ValueError("resource URI path contains an invalid segment")
        segments.append(segment)
    return parsed.scheme.lower(), parsed.netloc, tuple(segments)


class SkillFrontmatter(RootModel[dict[str, JsonValue]]):
    """Agent Skills frontmatter carried verbatim as JSON-compatible values."""

    @model_validator(mode="after")
    def validate_agent_skills_fields(self) -> SkillFrontmatter:
        name = self.root.get("name")
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not _SKILL_NAME_PATTERN.fullmatch(name)
        ):
            raise ValueError(
                "name must be 1-64 lowercase letters, digits, or hyphens; "
                "it cannot start or end with a hyphen or contain consecutive hyphens"
            )

        description = self.root.get("description")
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 1024
        ):
            raise ValueError("description must contain 1-1024 characters")

        for field_name in ("license", "allowed-tools"):
            value = self.root.get(field_name)
            if field_name in self.root and not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")

        compatibility = self.root.get("compatibility")
        if "compatibility" in self.root and not isinstance(compatibility, str):
            raise ValueError("compatibility must be a string")
        if isinstance(compatibility, str) and not 1 <= len(compatibility) <= 500:
            raise ValueError("compatibility must contain 1-500 characters")

        metadata = self.root.get("metadata")
        if "metadata" in self.root and (
            not isinstance(metadata, dict)
            or any(not isinstance(value, str) for value in metadata.values())
        ):
            raise ValueError("metadata must map string keys to string values")

        _reject_non_finite(self.root)
        return self

    @property
    def name(self) -> str:
        return cast("str", self.root["name"])

    @property
    def description(self) -> str:
        return cast("str", self.root["description"])

    @property
    def license(self) -> str | None:
        return cast("str | None", self.root.get("license"))

    @property
    def compatibility(self) -> str | None:
        return cast("str | None", self.root.get("compatibility"))

    @property
    def metadata(self) -> dict[str, str] | None:
        return cast("dict[str, str] | None", self.root.get("metadata"))

    @property
    def allowed_tools(self) -> str | None:
        return cast("str | None", self.root.get("allowed-tools"))


def _reject_non_finite(value: JsonValue) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("frontmatter numbers must be finite")
    if isinstance(value, list):
        for item in value:
            _reject_non_finite(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)


class SkillResource(BaseModel):
    """One file in a static skill manifest."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    digest: str = Field(pattern=_SHA256_PATTERN)
    size: int = Field(ge=0)

    @field_validator("size", mode="before")
    @classmethod
    def validate_size_type(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("size must be a non-negative integer")
        if isinstance(value, float) and (
            not math.isfinite(value) or not value.is_integer()
        ):
            raise ValueError("size must be a non-negative integer")
        return value


class Skill(BaseModel):
    """A complete SEP-2640 skill entry."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    frontmatter: SkillFrontmatter
    resources: list[SkillResource] | Literal["dynamic"]

    @model_validator(mode="after")
    def validate_entry(self) -> Skill:
        scheme, authority, path_segments = _parse_resource_uri(self.uri)
        if not path_segments or path_segments[-1] != "SKILL.md":
            raise ValueError("uri must identify a SKILL.md resource")

        skill_path = path_segments[:-1]
        uri_name = skill_path[-1] if skill_path else unquote(authority)
        if not uri_name or uri_name != self.frontmatter.name:
            raise ValueError(
                "the final skill-path segment in uri must equal frontmatter.name"
            )

        if isinstance(self.resources, list):
            resource_uris = [resource.uri for resource in self.resources]
            if len(resource_uris) != len(set(resource_uris)):
                raise ValueError("resources must contain each URI exactly once")
            if self.uri not in resource_uris:
                raise ValueError("resources must include the skill's SKILL.md URI")
            for resource_uri in resource_uris:
                resource_scheme, resource_authority, resource_path = (
                    _parse_resource_uri(resource_uri)
                )
                if (
                    resource_scheme != scheme
                    or resource_authority != authority
                    or len(resource_path) <= len(skill_path)
                    or resource_path[: len(skill_path)] != skill_path
                ):
                    raise ValueError(
                        "every resource URI must identify a file within the skill directory"
                    )
        return self


class ListSkillsParams(mcp_types.PaginatedRequestParams):
    """Parameters for `skills/list`."""


class GetSkillParams(mcp_types.RequestParams):
    """Parameters for `skills/get`."""

    uri: str


class ListSkillsResult(mcp_types.PaginatedResult, mcp_types.CacheableResult):
    """Result from `skills/list`."""

    skills: list[Skill]
    result_type: Literal["complete"] = "complete"


class GetSkillResult(mcp_types.CacheableResult):
    """Result from `skills/get`."""

    skill: Skill
    result_type: Literal["complete"] = "complete"


class ListSkillsRequest(mcp_types.Request[ListSkillsParams, Literal["skills/list"]]):
    """Client request envelope for `skills/list`."""

    method: Literal["skills/list"] = "skills/list"
    params: ListSkillsParams


class GetSkillRequest(mcp_types.Request[GetSkillParams, Literal["skills/get"]]):
    """Client request envelope for `skills/get`."""

    method: Literal["skills/get"] = "skills/get"
    params: GetSkillParams
