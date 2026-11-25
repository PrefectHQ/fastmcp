from __future__ import annotations

import hashlib
import json
import re

from pydantic import ValidationError

from flows.agentic_stack_plans import ReviewFeedback

MARKER = re.compile(
    r"^<!-- fastmcp-triage-review:v1 pr:([1-9][0-9]*) head:([a-f0-9]{40}) base:([a-f0-9]{40}) -->$"
)
PREFIX = "<!-- fastmcp-triage-feedback:v1 "


def normalized(body: str) -> str:
    return body.replace("\r\n", "\n")


def feedback_key(feedback: ReviewFeedback) -> dict:
    return {
        "outcome": feedback.outcome,
        "finding_ids": sorted(
            [finding.model_dump() for finding in feedback.finding_ids],
            key=lambda finding: finding["id"],
        ),
    }


def fingerprint(body: str, feedback: ReviewFeedback) -> str:
    content = {"body": normalized(body), **feedback_key(feedback)}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def attach_feedback(body: str, feedback: ReviewFeedback) -> str:
    first, content = normalized(body).split("\n\n", 1)
    metadata = {
        **feedback_key(feedback),
        "body_sha256": fingerprint(body, feedback),
    }
    return (
        first
        + "\n\n"
        + PREFIX
        + json.dumps(metadata, sort_keys=True)
        + " -->\n\n"
        + content
    )


def read_feedback(body: str, number: int, head: str) -> dict | None:
    """Parse intact structured feedback; ambiguity is handled by the delivery policy."""
    parts = normalized(body).split("\n\n", 2)
    marker = MARKER.fullmatch(parts[0])
    if (
        len(parts) != 3
        or marker is None
        or (int(marker[1]), marker[2]) != (number, head)
        or not parts[1].startswith(PREFIX)
        or not parts[1].endswith(" -->")
    ):
        return None
    try:
        metadata = json.loads(parts[1][len(PREFIX) : -4])
        expected = metadata.pop("body_sha256")
        feedback = ReviewFeedback.model_validate(metadata)
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
        return None
    original = parts[0] + "\n\n" + parts[2]
    if expected != fingerprint(original, feedback):
        return None
    return feedback_key(feedback)
