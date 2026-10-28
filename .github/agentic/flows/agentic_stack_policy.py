from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flows.agentic_intake import revision_evidence
from flows.agentic_pr_verification import VerificationSpec
from flows.agentic_stack import StackSpec, digest


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issue_number: int = Field(gt=0)
    mode: Literal["fix", "mre", "review"] = "fix"
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


def publication_revision(
    evidence: dict, spec: StackSpec | None = None, layers: list[dict] | None = None
) -> str:
    if evidence.get("complete") is not True or evidence.get("schema_version") != 2:
        raise ValueError("Publication needs complete evidence")
    value = revision_evidence(evidence)
    own = set()
    if spec is not None:
        manifest = {layer["branch"]: layer for layer in layers or []}
        for pr in value["prs"]:
            expected = manifest.get(pr["head_ref"])
            if expected is None:
                continue
            if (
                pr["head_repository"] != "PrefectHQ/fastmcp"
                or pr["head_sha"] != expected["head_sha"]
                or pr["user"]["id"] != spec.publisher_id
            ):
                raise ValueError("Publication branch names do not prove ownership")
            own.add(pr["number"])
    value["prs"] = [pr for pr in value["prs"] if pr["number"] not in own]
    value["timeline"] = [
        event
        for event in value["timeline"]
        if not (
            event["event"] == "cross-referenced"
            and event.get("source", {}).get("issue", {}).get("number") in own
            and event["source"]["issue"]
            .get("html_url", "")
            .startswith("https://github.com/PrefectHQ/fastmcp/pull/")
        )
    ]
    value["stacks"] = [
        stack
        for stack in value["stacks"]
        if any(pr["number"] not in own for pr in stack["pull_requests"])
    ]
    for key in ("collected_at", "searches", "repository_labels"):
        value.pop(key, None)
    value["issue"].pop("updated_at", None)
    return digest(value)
