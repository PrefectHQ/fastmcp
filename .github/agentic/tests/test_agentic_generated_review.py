from http.client import RemoteDisconnected

import pytest

from flows.agentic_stack_publication import GitHub
from tests.test_agentic_review_publication import BOT


def generated_draft(endpoint, repository):
    import subprocess
    import sys

    from flows.agentic_stack import assemble_stack
    from flows.agentic_stack_publication import REPOSITORY, pr_body

    state, url, _ = endpoint
    repo, original = repository
    spec = original.model_copy(update={"layers": original.layers[:1]})

    def check(directory, paths):
        cmd = [sys.executable, "tests/probe.py"]
        result = subprocess.run(cmd, cwd=directory, check=True)
        return [{"command": cmd, "exit_code": result.returncode}]

    artifact = assemble_stack(repo, spec, check)
    manifest = artifact["layers"][0]
    state["pr"].update(
        draft=True,
        title=spec.layers[0].title,
        body=pr_body(spec.layers[0]),
        user={"login": spec.publisher_login, "id": spec.publisher_id},
        head={
            "ref": manifest["branch"],
            "sha": manifest["head_sha"],
            "repo": {"full_name": REPOSITORY},
        },
        base={
            "ref": manifest["base_ref"],
            "sha": manifest["parent_sha"],
            "repo": {"full_name": REPOSITORY},
        },
    )
    publication = {
        "pull_requests": [7],
        "spec_sha256": artifact["spec_sha256"],
        "bundle_sha256": artifact["bundle_sha256"],
    }
    return state, GitHub("test-installation", url), spec, artifact, publication


def test_generated_draft_review_survives_lost_response(endpoint, repository):
    from flows.agentic_generated_review import deliver_generated_reviews
    from flows.agentic_stack_plans import Review

    state, client, spec, artifact, publication = generated_draft(endpoint, repository)
    approval = Review(
        decision="approved", reason="Supported controls verified.", findings=[]
    )
    state["disconnect"] = True
    args = (
        client,
        BOT,
        spec,
        artifact,
        publication,
        approval,
        "validation-run",
        "review-run",
        lambda: None,
    )
    with pytest.raises(RemoteDisconnected):
        deliver_generated_reviews(*args)
    result = deliver_generated_reviews(*args)
    assert result["posted"][0]["status"] == "already_posted"
    assert len(state["reviews"]) == 1
    review = state["reviews"][0]
    assert review["state"] == "COMMENTED"
    assert review["commit_id"] == artifact["layers"][0]["head_sha"]
    assert "tests/probe.py`" in review["body"] and "exit 0" in review["body"]
    assert "review-run" in review["body"] and "not merge approval" in review["body"]
    visible, details = review["body"].split("<details>", 1)
    assert "FastMCP triage reviewer bot" in visible
    assert "Patch review passed" in visible
    assert "tests/probe.py" not in visible
    assert "Supported controls verified." in details
    assert "tests/probe.py" in details
    assert state["pr"]["draft"] is True


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "author",
        "base",
        "head",
        "ready",
        "hold",
        "artifact",
        "review",
        "freshness",
    ],
)
def test_generated_review_rejects_changed_draft(endpoint, repository, change):
    from flows.agentic_generated_review import deliver_generated_reviews
    from flows.agentic_stack_plans import Review

    state, client, spec, artifact, publication = generated_draft(endpoint, repository)
    approval = Review(decision="approved", reason="Verified controls.", findings=[])
    if change == "body":
        state["pr"]["body"] += "\nHuman correction"
    if change == "author":
        state["pr"]["user"]["id"] += 1
    if change in {"base", "head"}:
        state["pr"][change]["sha"] = "c" * 40
    if change == "ready":
        state["pr"]["draft"] = False
    if change == "hold":
        state["pr"]["labels"] = [{"name": "do-not-merge"}]
    if change == "artifact":
        publication["bundle_sha256"] = "c" * 64
    if change == "review":
        approval.findings.append("Reachable regression")

    def guard():
        if change == "freshness":
            raise ValueError("New evidence")

    with pytest.raises(ValueError):
        deliver_generated_reviews(
            client,
            BOT,
            spec,
            artifact,
            publication,
            approval,
            "validation-run",
            "review-run",
            guard,
        )
    assert not state["posts"]


@pytest.mark.parametrize("kind", ["comment", "inline", "review", "edited_own_review"])
@pytest.mark.parametrize("timing", ["before_delivery", "before_post"])
def test_generated_review_rejects_unreviewed_discussion(
    endpoint, repository, kind, timing
):
    from flows.agentic_generated_review import deliver_generated_reviews
    from flows.agentic_stack_plans import Review

    state, client, spec, artifact, publication = generated_draft(endpoint, repository)
    approval = Review(
        decision="approved", reason="Verified supported controls.", findings=[]
    )
    args = (
        client,
        BOT,
        spec,
        artifact,
        publication,
        approval,
        "validation-run",
        "review-run",
        lambda: None,
    )
    if kind == "edited_own_review":
        deliver_generated_reviews(*args)
        state["posts"].clear()

    def change():
        if kind == "comment":
            state["comments"].append(
                {
                    "id": 123,
                    "body": "Supported regression; stop and review",
                    "user": {"login": "maintainer", "type": "User"},
                }
            )
        elif kind == "inline":
            state["review_comments"].append(
                {
                    "id": 124,
                    "body": "This breaks the caller",
                    "user": {"login": "other-reviewer", "type": "Bot"},
                }
            )
        elif kind == "review":
            state["reviews"].append(
                {
                    "id": 125,
                    "body": "Changes required",
                    "state": "CHANGES_REQUESTED",
                    "commit_id": artifact["layers"][0]["head_sha"],
                    "user": {"login": "maintainer", "type": "User"},
                }
            )
        else:
            state["reviews"][0]["body"] += "\nEdited after posting"

    if timing == "before_delivery":
        change()
    else:
        reads = 0

        def before_get(path):
            nonlocal reads
            if "/reviews?" in path:
                reads += 1
                if reads == 2:
                    change()

        state["before_get"] = before_get
    if kind == "edited_own_review" and timing == "before_post":
        state["before_get"] = lambda path: None
        change()
    with pytest.raises(ValueError, match="discussion|review evidence"):
        deliver_generated_reviews(*args)
    assert not state["posts"]


def test_delivery_task_retries_lost_post_without_revalidating_patch(
    endpoint, repository, evidence, monkeypatch
):
    from datetime import UTC, datetime, timedelta
    from functools import partial
    from uuid import uuid4

    from prefect import flow
    from prefect.blocks.system import Secret
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.actions import ArtifactCreate
    from prefect.client.schemas.filters import FlowRunFilter
    from prefect.states import Completed, Running
    from prefect.testing.utilities import prefect_test_harness

    import flows.agentic_generated_review as delivery
    from flows.agentic_review_app import ReviewApp, review_client
    from flows.agentic_stack import validate_stack
    from flows.agentic_stack_policy import Batch
    from flows.agentic_stack_runtime import Runtime, record, saved

    state, github, spec, artifact, publication = generated_draft(endpoint, repository)
    evidence = {**evidence, "stacks": []}
    _, url, pem = endpoint

    @flow
    def review_flow():
        return None

    @flow
    def driver(owner, batch, runtime, publication, parameters):
        return delivery.deliver_published_reviews.with_options(retry_delay_seconds=0)(
            owner, batch, runtime, publication, parameters
        )

    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(review_flow, state=Running())
        validator_flow = client.create_flow(validate_stack)
        validator = client.create_deployment(
            validator_flow, "validator", version="a" * 40
        )
        validation = client.create_flow_run_from_deployment(
            validator, parameters={"spec": spec.model_dump(mode="json")}
        )
        client.set_flow_run_state(validation.id, Completed(), force=True)
        client.create_artifact(
            ArtifactCreate(
                key=f"fastmcp-stack-{spec.batch_id}-{spec.group}",
                type="fastmcp-stack-validation",
                flow_run_id=validation.id,
                data=artifact,
            )
        )
        parameters = {
            "instructions": {},
            "context": {
                "spec": spec.model_dump(mode="json"),
                "layers": artifact["layers"],
                "validation_run_id": str(validation.id),
                "items": [{"evidence": evidence}],
            },
        }
        review = client.create_flow_run(
            review_flow, parameters=parameters, state=Completed()
        )
        version = uuid4()
        runtime = Runtime(
            plans={
                role: {"flow_id": review.flow_id, "version_id": version}
                for role in [
                    "assess",
                    "reproduce",
                    "prepare",
                    "review",
                    "triage_review",
                ]
            },
            validator_id=validator,
            source_sha="a" * 40,
            publish_reviews=True,
            reviewer_app=ReviewApp(
                app_id=42, slug="reviewer", private_key_block="generated-review-key"
            ),
        )
        now = datetime.now(UTC)
        batch = Batch(
            id=spec.batch_id,
            started_at=now,
            expires_at=now + timedelta(minutes=2),
            groups=[
                {
                    "name": spec.group,
                    "rationale": "Deliver retained exact patch review",
                    "branch": "main",
                    "base_sha": spec.trunk_sha,
                    "items": [{"issue_number": 1}],
                }
            ],
        )
        publication.update(
            validation_run_id=str(validation.id),
            review_run_id=str(review.id),
            controller_run_id=str(owner.id),
        )
        record.fn(owner.id, f"{batch.id}-{spec.group}-published", publication)
        Secret(value=pem).save("generated-review-key", overwrite=True)
        state["routes"] = {
            f"/flow_runs/{review.id}/execution-plan": {
                "status": "completed",
                "snapshot": {"execution_plan_version_id": str(version)},
            },
            f"/flow_runs/{review.id}/execution-plan/outputs/result": {
                "decision": {
                    "decision": "approved",
                    "reason": "Supported base/head controls independently verified.",
                    "findings": [],
                }
            },
            "/evidence": evidence,
        }
        monkeypatch.setattr(
            delivery.api, "fn", lambda method, path: github.request(path.lstrip("/"))
        )
        monkeypatch.setattr(
            delivery.collect, "fn", lambda *args, **kwargs: github.request("evidence")
        )
        monkeypatch.setattr(delivery, "review_client", partial(review_client, url=url))
        state["disconnect"] = True
        result = driver(owner.id, batch, runtime, publication, parameters)
        assert result["posted"][0]["status"] == "already_posted"
        assert len(state["reviews"]) == 1
        assert (
            len([path for path, _ in state["posts"] if path.endswith("/access_tokens")])
            == 2
        )
        assert saved.fn(owner.id, f"{batch.id}-{spec.group}-published") == publication
        assert (
            saved.fn(owner.id, f"{batch.id}-{spec.group}-generated-reviews") == result
        )
        validations = client.read_flow_runs(
            flow_run_filter=FlowRunFilter(id={"any_": [validation.id]})
        )
        assert len(validations) == 1
