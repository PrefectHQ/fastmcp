from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flows.agentic_intake import revision_evidence
from flows.agentic_pr_verification import VerificationSpec
from flows.agentic_stack import digest


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issue_number: int = Field(gt=0)
    mode: Literal["mre", "review"] = "mre"
    verification_probes: dict[str, str] = Field(default_factory=dict, max_length=5)

    @field_validator("verification_probes")
    @classmethod
    def bounded_probes(cls, value: dict[str, str]) -> dict[str, str]:
        return VerificationSpec.test_files(value)


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    rationale: str = Field(min_length=1, max_length=2000)
    branch: str = Field(pattern=r"^(main|release/[23]\.x)$")
    base_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    items: list[Item] = Field(min_length=1, max_length=3)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    started_at: datetime
    expires_at: datetime
    groups: list[Group] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def bounded(self) -> Batch:
        if self.started_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Batch dates require a timezone")
        if (
            not self.started_at
            < self.expires_at
            <= self.started_at + timedelta(hours=2)
        ):
            raise ValueError("Batch window must be positive and at most two hours")
        if len({group.name for group in self.groups}) != len(self.groups):
            raise ValueError("Group names must be unique")
        issues = [item.issue_number for group in self.groups for item in group.items]
        if len(set(issues)) != len(issues):
            raise ValueError("An issue may occur only once per batch")
        return self


def publication_revision(evidence: dict) -> str:
    if evidence.get("complete") is not True or evidence.get("schema_version") != 2:
        raise ValueError("Publication needs complete evidence")
    value = revision_evidence(evidence)
    value["stacks"] = [stack for stack in value["stacks"] if stack["pull_requests"]]
    for key in ("collected_at", "searches", "repository_labels"):
        value.pop(key, None)
    value["issue"].pop("updated_at", None)
    return digest(value)


def review_revision(evidence: dict) -> str:
    value = revision_evidence(evidence)
    timelines = [value.get("timeline", [])]
    for pr in value.get("prs", []):
        for field in ("checks", "statuses", "updated_at"):
            pr.pop(field, None)
        timelines.append(pr.get("timeline", []))
    for timeline in timelines:
        timeline[:] = [event for event in timeline if not _foreign_reference(event)]
    return publication_revision(value)


def _foreign_reference(event: dict) -> bool:
    if event.get("event") != "cross-referenced":
        return False
    source = (event.get("source") or {}).get("issue") or {}
    repositories = []
    name = (source.get("repository") or {}).get("full_name")
    if name:
        repositories.append(name.lower())
    url = urlsplit(source.get("html_url") or "")
    parts = url.path.strip("/").split("/")
    if url.hostname == "github.com" and len(parts) >= 4:
        repositories.append("/".join(parts[:2]).lower())
    return bool(repositories) and all(
        name != "prefecthq/fastmcp" for name in repositories
    )
