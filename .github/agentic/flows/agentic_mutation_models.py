from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class NormEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: Literal[
        "scope_contract",
        "prior_discussion",
        "public_history",
        "reporter_first_claim",
        "bot_feedback",
        "current_fix_and_reverts",
        "canonical_equivalence",
        "settled_policy",
        "maintainer_direction",
        "coverage_parity",
    ]
    satisfied: bool = False
    summary: str = Field(min_length=1, max_length=1000)
    evidence_urls: list[HttpUrl] = Field(min_length=1, max_length=3)


class MutationRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "assign_issue",
        "finish_pr",
        "review_pr",
        "close_duplicate",
        "close_completed",
        "close_settled",
        "close_pr",
    ]
    target_number: int = Field(gt=0)
    related_pr: int | None = Field(default=None, gt=0)
    head_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    base_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=3000)
    next_step: str = Field(min_length=1, max_length=3000)
    scope_notes: str = Field(default="", max_length=3000)
    assignee: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,39}$")
    canonical_issue: int | None = Field(default=None, gt=0)
    merged_pr: int | None = Field(default=None, gt=0)
    canonical_pr: int | None = Field(default=None, gt=0)
    canonical_head_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    canonical_base_sha: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    policy_url: HttpUrl | None = None
    public_comment: str = Field(default="", max_length=6000)
    norm_evidence: list[NormEvidence] = Field(default_factory=list, max_length=10)

    @field_validator("norm_evidence")
    @classmethod
    def distinct_checks(cls, value: list[NormEvidence]) -> list[NormEvidence]:
        if len({item.check for item in value}) != len(value):
            raise ValueError("Norm checks must be unique")
        return value
