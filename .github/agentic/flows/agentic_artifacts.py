from __future__ import annotations

import html
import re
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter


def plain(value: object) -> str:
    if value is None:
        return "unknown"
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"([\\`*{}\[\]()#+.!_~-])", r"\\\1", text)
    return html.escape(text, quote=False).replace("|", "&#124;")


def published_reviews(result: dict) -> list[dict]:
    receipts = {}

    def collect(item: dict) -> None:
        posted = [
            *item.get("published_reviews", []),
            *item.get("review_delivery", {}).get("posted", []),
        ]
        for receipt in posted:
            identity = (receipt["pr_number"], receipt["review_id"])
            if identity in receipts and {
                key: value
                for key, value in receipts[identity].items()
                if key != "status"
            } != {key: value for key, value in receipt.items() if key != "status"}:
                raise ValueError("Conflicting retained public review receipts")
            receipts[identity] = receipt
        for retained in item.get("retained_outcomes", []):
            collect(retained["result"])

    for items in result.get("bins", {}).values():
        for item in items:
            collect(item)
    return list(receipts.values())


def retain_markdown(owner: UUID, key: str, markdown: str) -> str:
    return retain_artifact(
        owner, key, markdown, "markdown", "FastMCP maintainer review"
    )


def retain_artifact(
    owner: UUID, key: str, data: dict | str, artifact_type: str, description: str
) -> str:
    with get_client(sync_client=True) as client:
        previous = client.read_artifacts(
            artifact_filter=ArtifactFilter(key={"any_": [key]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}),
            limit=2,
        )
        if previous:
            if (
                len(previous) != 1
                or previous[0].data != data
                or previous[0].type != artifact_type
            ):
                raise ValueError("Conflicting retained artifact")
            return str(previous[0].id)
        return str(
            client.create_artifact(
                ArtifactCreate(
                    key=key,
                    type=artifact_type,
                    flow_run_id=owner,
                    data=data,
                    description=description,
                )
            ).id
        )
