from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from flows.agentic_stack_plans import COMMON, OBJECTIVES, Assessment, Reproduction

BRANCHES = {
    "verify": ("existing_review",),
    "reproduce": ("reproduce",),
    "record": ("evidence_needed", "prune", "maintainer", "skip"),
    "blocked": ("environment_failure",),
}
TERMINAL = ("completed", "failed", "crashed", "cancelled", "timed_out")


def assessment_branch(value: dict) -> str:
    decision = Assessment.model_validate(value).decision
    return next(name for name, choices in BRANCHES.items() if decision in choices)


def build_triage_plan(models: dict, verify_id: UUID, finalize_id: UUID) -> dict:
    orchestration = {
        "output_selection": "exactly_one",
        "evaluate_when": "all_required_inputs_produced",
        "on_failure": {"retry": {"max_attempts": 1}},
        "limits": {"max_activations": 1, "max_concurrent": 1},
    }
    object_port = {"schema": {"type": "object"}}
    assessment_outputs = {}
    for name, choices in BRANCHES.items():
        schema = deepcopy(Assessment.model_json_schema())
        schema["properties"]["decision"] = {"type": "string", "enum": list(choices)}
        assessment_outputs[name] = {"schema": schema}

    def agent(role: str, inputs: dict, outputs: dict, objective: str) -> dict:
        return {
            "kind": "AgentNode",
            "label": "Assess issue · Luna"
            if role == "triage"
            else "Reproduce bug · Luna",
            "model": {
                "connection_id": models["connection_id"],
                **models["roles"][role],
            },
            "inputs": inputs,
            "outputs": outputs,
            "objective": objective,
            "orchestration": deepcopy(orchestration),
        }

    def deployment(deployment_id: UUID, label: str) -> dict:
        return {
            "kind": "DeploymentNode",
            "label": label,
            "deployment": {"id": str(deployment_id)},
            "wait": {"for": "child_flow_run", "until": "terminal"},
            "inputs": {"parameters": deepcopy(object_port)},
            "outputs": {name: deepcopy(object_port) for name in TERMINAL},
            "orchestration": deepcopy(orchestration),
        }

    assess = agent(
        "triage",
        {"request": deepcopy(object_port)},
        assessment_outputs,
        "The request contains instructions and context. References below to instructions "
        "and context mean request.instructions and request.context.\n"
        + COMMON
        + "\n"
        + OBJECTIVES["assess"]
        + "\nSelect the output matching your decision. In review-only mode, do not select "
        "reproduce: record the specific remaining decision as maintainer instead. "
        "existing_review is only for a contribution listed in context.evidence.prs. When "
        "the pinned base already resolves the report through a commit or existing option "
        "and no contribution is listed, record maintainer with that evidence and a question "
        "asking to confirm and close.",
    )
    reproduce = agent(
        "reproduce",
        {
            "request": deepcopy(object_port),
            "assessment": {"schema": Assessment.model_json_schema()},
        },
        {"result": {"schema": Reproduction.model_json_schema()}},
        "The request contains instructions and context. References below to instructions "
        "and context mean request.instructions and request.context. The separate assessment "
        "input is the assessment for this exact issue. If request.allowed is false, do not use "
        "tools or reproduce: return environment_failure explaining the disallowed branch.\n"
        + COMMON
        + "\n"
        + OBJECTIVES["reproduce"],
    )
    verify = deployment(verify_id, "Verify existing contributions · deterministic")
    verify["inputs"]["assessment"] = {"schema": Assessment.model_json_schema()}
    finalize = deployment(finalize_id, "Retain decision and evidence · deterministic")
    finalize["inputs"]["outcomes"] = {
        "schema": {"type": "object"},
        "expects": {
            "shape": "source_map",
            "cardinality": "zero_or_more",
            "key_by": "edge",
        },
    }
    finalize["orchestration"].update(
        {
            "evaluate_when": "all_reachable_terminal",
            "on_upstream": {
                state: "allow" for state in TERMINAL if state != "completed"
            },
        }
    )
    edges = []

    def connect(source: dict, node: str, port: str) -> None:
        edges.append(
            {
                "id": f"edge_{len(edges)}",
                "from": source,
                "to": {"node": node, "input": port},
            }
        )

    def plan_input(name: str, node: str, port: str) -> None:
        connect({"type": "plan_input", "input": name}, node, port)

    def output(node: str, name: str, target: str, port: str) -> None:
        connect({"type": "node_output", "node": node, "output": name}, target, port)

    plan_input("assess_request", "assess", "request")
    plan_input("reproduce_request", "reproduce", "request")
    for node in ("verify", "finalize"):
        plan_input("parameters", node, "parameters")
    for branch in BRANCHES:
        if branch in {"verify", "reproduce"}:
            output("assess", branch, branch, "assessment")
        output("assess", branch, "finalize", "outcomes")
    output("reproduce", "result", "finalize", "outcomes")
    for state in TERMINAL:
        output("verify", state, "finalize", "outcomes")
    return {
        "schema_version": "0.2",
        "kind": "ExecutionPlan",
        "inputs": {
            "parameters": {**deepcopy(object_port), "required": True},
            "assess_request": {**deepcopy(object_port), "required": True},
            "reproduce_request": {**deepcopy(object_port), "required": True},
        },
        "nodes": {
            "assess": assess,
            "verify": verify,
            "reproduce": reproduce,
            "finalize": finalize,
        },
        "edges": edges,
        "outputs": {
            "result": {
                "fields": {
                    "finalizer": {
                        "schema": {"type": "object"},
                        "source": {
                            "type": "node_output",
                            "node": "finalize",
                            "output": "completed",
                        },
                    }
                },
                "required": ["finalizer"],
            },
        },
        "orchestration": {
            "limits": {"max_active_nodes": 1, "max_active_child_flow_runs": 1}
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-deployment", type=UUID, required=True)
    parser.add_argument("--finalize-deployment", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    models = json.loads(
        (
            Path(__file__).resolve().parents[1] / "workflows/stack-models.json"
        ).read_text()
    )
    plan = build_triage_plan(
        models, arguments.verify_deployment, arguments.finalize_deployment
    )
    arguments.output.write_text(json.dumps(plan, indent=2) + "\n")
