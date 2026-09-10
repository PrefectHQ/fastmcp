"""Wire models for the MCP Skills extension (SEP-2640)."""

from __future__ import annotations

import math
import re
from typing import Literal
from urllib.parse import unquote, urlsplit

import mcp_types
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

_SKILL_NAME_PATTERN = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]+(?<!-)$")
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class SkillFrontmatter(BaseModel):
    """Agent Skills frontmatter carried verbatim as JSON-compatible values."""

    model_config = ConfigDict(extra="allow")

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] | None = None
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @model_validator(mode="after")
    def validate_agent_skills_fields(self) -> SkillFrontmatter:
        if len(self.name) > 64 or not _SKILL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                "name must be 1-64 lowercase letters, digits, or hyphens; "
                "it cannot start or end with a hyphen or contain consecutive hyphens"
            )
        if not self.description.strip() or len(self.description) > 1024:
            raise ValueError("description must contain 1-1024 characters")
        if self.compatibility is not None and len(self.compatibility) > 500:
            raise ValueError("compatibility must contain at most 500 characters")
        _reject_non_finite(self.__pydantic_extra__)
        return self


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


class Skill(BaseModel):
    """A complete SEP-2640 skill entry."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    frontmatter: SkillFrontmatter
    resources: list[SkillResource] | Literal["dynamic"]

    @model_validator(mode="after")
    def validate_entry(self) -> Skill:
        parsed = urlsplit(self.uri)
        path_segments = [unquote(part) for part in parsed.path.split("/") if part]
        if not parsed.scheme or not path_segments or path_segments[-1] != "SKILL.md":
            raise ValueError("uri must identify a SKILL.md resource")

        skill_path = path_segments[:-1]
        uri_name = skill_path[-1] if skill_path else unquote(parsed.netloc)
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


class GetSkillResult(mcp_types.Result):
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
