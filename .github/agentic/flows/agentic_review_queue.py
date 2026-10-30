from __future__ import annotations

import html
import json
import re
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter

from flows.agentic_mutation_batch import mutation_batch


def plain(value: object) -> str:
    if value is None:
        return "unknown"
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"([\\`*{}\[\]()#+.!_~-])", r"\\\1", text)
    return html.escape(text, quote=False).replace("|", "&#124;")


def code_block(value: str, language: str = "text") -> str:
    fence = "`" * max(
        3, 1 + max((len(run) for run in re.findall(r"`+", value)), default=0)
    )
    return f"{fence}{language}\n{value}\n{fence}"


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


def review_markdown(result: dict) -> str:
    sections = {
        "existing_review": "Existing contributions",
        "evidence": "Reproductions and missing evidence",
        "pruning": "Closure candidates — approval required",
        "decisions": "Maintainer decisions",
        "operational": "Operational attention",
        "unchanged": "No action this pass",
        "stacks": "Draft fixes",
    }
    lines = [
        f"# FastMCP triage: {plain(result['batch_id'])}",
        "",
        "Review evidence at the recorded revisions. Recheck current GitHub state before acting. "
        "Closure candidates are proposals; a passing test is not merge approval.",
    ]
    posted = published_reviews(result)
    if posted:
        lines.extend(["", "## Published reviews", ""])
        for receipt in posted:
            lines.extend(
                [
                    f"[Posted review on PR #{receipt['pr_number']}]({receipt['review_url']}) "
                    f"at `{receipt['head_sha']}`.",
                    "",
                ]
            )
        lines.extend(
            [
                "Publication is a recorded side effect. Later freshness stops below "
                "still require rechecking the current contribution before acting.",
                "",
            ]
        )
    mutations = mutation_batch(result)["recommendations"]
    if mutations:
        lines.extend(
            [
                "",
                "## Proposed mutations",
                "",
                "Every row requires maintainer approval. No mutations are executed by this queue.",
                "",
                "| Action | Target | Evidence | Remaining gates | Next step |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for mutation in mutations:
            target = mutation["target"]
            revision = (
                mutation.get("head_sha") or mutation.get("baseline_sha") or "unbound"
            )
            gates = (
                ", ".join(mutation["remaining_gates"]) or "Approval and live preflight"
            )
            lines.append(
                f"| {plain(mutation['action'])} | [#{target['number']}]({target['url']}) | {plain(revision)} | {plain(gates)} | {plain(mutation['next_step'])} |"
            )
        for mutation in mutations:
            target = mutation["target"]
            lines.extend(
                [
                    "",
                    f"### {plain(mutation['action'])} on {plain(target['kind'])} #{target['number']}",
                    "",
                    "Reason: " + plain(mutation["reason"]),
                    "Scope: " + plain(mutation["scope_notes"]),
                    "Before merge: "
                    + plain(
                        ", ".join(mutation.get("before_merge", []))
                        or "Not a merge proposal"
                    ),
                    "",
                ]
            )
            if mutation.get("payload"):
                lines.extend(
                    [
                        "Proposed payload:",
                        "",
                        code_block(json.dumps(mutation["payload"], indent=2), "json"),
                        "",
                    ]
                )
            for check in mutation.get("norm_evidence", []):
                lines.append(
                    f"- {plain(check['check'])} ({'passed' if check['satisfied'] else 'unmet'}): {plain(check['summary'])} Sources: {plain(', '.join(check['evidence_urls']))}"
                )
            lines.append("")
    for key, title in sections.items():
        items = result["bins"].get(key, [])
        if not items:
            continue
        lines.extend(["", f"## {title}", ""])
        for item in items:
            number = item.get("issue_number")
            label = (
                f"[Issue #{number}](https://github.com/PrefectHQ/fastmcp/issues/{number})"
                if isinstance(number, int)
                else plain(item.get("group", "Group"))
            )
            lines.extend([f"### {label}", ""])
            next_step = item.get("next_step")
            adversarial = item.get("adversarial_review")
            if adversarial:
                lines.extend(
                    [
                        f"**Adversarial disposition:** {plain(adversarial['decision'])}",
                        "",
                        plain(adversarial["reason"]),
                        "",
                    ]
                )
                lines.extend(
                    f"- {plain(finding)}" for finding in adversarial["findings"]
                )
                lines.append("")
            if next_step:
                lines.extend([f"Next: {plain(next_step)}", ""])
            for pr_number in item.get("pull_requests", []):
                lines.extend(
                    [
                        f"[Draft PR #{pr_number}](https://github.com/PrefectHQ/fastmcp/pull/{pr_number})",
                        "",
                    ]
                )
            if item.get("validation_run_id"):
                lines.extend(
                    [
                        "Validated by run " + plain(item["validation_run_id"]) + ". "
                        "Independent review: " + plain(item.get("review_run_id")) + ".",
                        "",
                    ]
                )
            assessment = item.get("assessment", item)
            reason = assessment.get("reason") or item.get("detail") or item.get("error")
            if reason:
                if item.get("verification"):
                    lines.extend(["Assessment before verification:", ""])
                lines.extend([plain(reason), ""])
            if "observed" in item and "base_sha" in item:
                lines.extend(
                    [
                        f"**Reproduction outcome:** {plain(item.get('decision'))}.",
                        f"Baseline: {plain(item['base_sha'])}.",
                        "",
                    ]
                )
                if item.get("contract"):
                    lines.extend(["Contract: " + plain(item["contract"]), ""])
                if item.get("command"):
                    lines.extend(
                        ["Command:", "", code_block(item["command"], "sh"), ""]
                    )
                lines.extend(["Observed:", "", code_block(item["observed"]), ""])
                if item.get("regression_patch"):
                    lines.extend(
                        [
                            "Regression probe (not a fix):",
                            "",
                            code_block(item["regression_patch"], "diff"),
                            "",
                        ]
                    )
            if item.get("status") == "stale":
                lines.extend(["**Evidence changed. Refresh before acting.**", ""])
            held = [
                str(pr["number"])
                for pr in item.get("route", {}).get("pull_requests", [])
                if pr.get("held")
            ]
            if held:
                lines.extend(
                    [f"**Contribution holds retained:** #{', #'.join(held)}.", ""]
                )
            for check in item.get("verification", []):
                pr = check["pr_number"]
                comparison = check.get("comparison", {})
                regressions = comparison.get("regressed_tests", [])
                status = check["status"]
                if status == "environment_failure":
                    verdict = "Verification could not complete; repair the environment."
                elif status == "evidence_needed":
                    verdict = "A regression test or independent reproduction is needed."
                elif regressions:
                    verdict = (
                        "A regression was reproduced; repair it before merge review."
                    )
                elif check["tests"]["head"]["status"] != "passed" or check["tests"][
                    "adjacent"
                ]["status"] not in {"passed", "not_run"}:
                    verdict = "Executed checks need investigation."
                elif check["tests"]["adjacent"]["status"] == "not_run":
                    verdict = "Head checks passed; adjacent checks were not run. Compatibility and merge review remain."
                else:
                    verdict = (
                        "Executed checks passed; compatibility and merge review remain."
                    )
                lines.extend(
                    [
                        f"[PR #{pr}](https://github.com/PrefectHQ/fastmcp/pull/{pr}): **{verdict}**",
                        "",
                        f"Head `{check['head_sha'][:12]}` against `{str(check.get('baseline_sha'))[:12]}`. "
                        f"{len(comparison.get('fixed_tests', []))} fail-to-pass cases; "
                        f"{len(regressions)} pass-to-fail cases. Verification run `{check['run_id']}`.",
                        "",
                    ]
                )
                for limitation in check.get("limitations", []):
                    lines.extend([f"Limitation: {plain(limitation)}", ""])
                adjacent = check.get("tests", {}).get("adjacent", {})
                if adjacent.get("status") == "not_run" and adjacent.get("reason"):
                    lines.extend([f"Adjacent checks: {plain(adjacent['reason'])}", ""])
                for case in regressions:
                    lines.append(f"- {plain(case)}")
                if regressions:
                    lines.append("")
            question = assessment.get("question")
            if question:
                lines.extend([f"**Decision needed:** {plain(question)}", ""])
    usage = result.get("usage", {})
    lines.extend(
        [
            "",
            "## Usage",
            "",
            f"Observed input tokens: {plain(usage.get('input_tokens', 'unknown'))}. "
            f"Output tokens: {plain(usage.get('output_tokens', 'unknown'))}. "
            "Reused assessments exclude historical usage. Dollar cost is unknown.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def retain_review_queue(owner: UUID, result: dict) -> str:
    retain_artifact(
        owner,
        f"{result['batch_id']}-mutation-batch",
        mutation_batch(result, str(owner)),
        "fastmcp-mutation-batch",
        "Approval-required FastMCP mutation proposals; execution disabled",
    )
    return retain_markdown(
        owner, f"{result['batch_id']}-mutation-review-queue", review_markdown(result)
    )


def retain_markdown(owner: UUID, key: str, markdown: str) -> str:
    return retain_artifact(
        owner, key, markdown, "markdown", "FastMCP maintainer review queue"
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
                raise ValueError("Conflicting review queue artifact")
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
