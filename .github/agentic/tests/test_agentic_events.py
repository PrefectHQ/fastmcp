import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from prefect.server.events.schemas.automations import AutomationCore
from prefect.server.events.schemas.events import Event
from prefect.server.utilities.user_templates import (
    register_user_template_filters,
    render_user_template_sync,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def automations(tmp_path):
    executable = tmp_path / "uv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv\n"
        "if args[args.index('api') + 1] == 'POST':\n"
        "    data = json.loads(pathlib.Path(args[-1][1:]).read_text())\n"
        "    if 'name' in data:\n"
        "        target = pathlib.Path(os.environ['CAPTURE']) / (data['name'] + '.json')\n"
        "        target.write_text(json.dumps(data))\n"
        "        print(json.dumps({'id': '11111111-1111-4111-8111-111111111111'}))\n"
        "    else:\n"
        "        print(json.dumps([{'id': os.environ['INTAKE_ID']}]) if 'deployments' in data else '[]')\n"
    )
    executable.chmod(0o755)
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    controller, intake, notify = (str(uuid4()) for _ in range(3))
    subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/agentic_stack.sh"),
            "events",
            str(runtime),
            controller,
            intake,
            notify,
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CAPTURE": str(tmp_path),
            "INTAKE_ID": intake,
            "PREFECT_API_URL": "https://api.prefect.cloud/api/accounts/a/workspaces/w",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/agentic_stack.sh"),
            "alerts",
            str(runtime),
            notify,
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CAPTURE": str(tmp_path),
            "INTAKE_ID": intake,
            "PREFECT_API_URL": "https://api.prefect.cloud/api/accounts/test/workspaces/test",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    configs = {
        p.stem: json.loads(p.read_text()) for p in tmp_path.glob("fastmcp-*.json")
    }
    for name, config in configs.items():
        if name != "fastmcp-triage-github":
            AutomationCore.model_validate(config)
    return configs, controller, intake


def github_event(
    configs,
    *,
    action="opened",
    draft=False,
    body="Fixes #123",
    changes=None,
    kind="pr",
    sender="User",
):
    payload = {
        "action": action,
        "repository": {"full_name": "PrefectHQ/fastmcp"},
        "sender": {"type": sender, "login": "zzstoatzz"},
        "pull_request" if kind == "pr" else "issue": {
            "number": 6089,
            "draft": draft,
            "body": body,
        },
        "changes": changes or {},
    }
    rendered = render_user_template_sync(
        configs["fastmcp-triage-github"]["template"],
        {
            "body": payload,
            "headers": {"x-github-event": "pull_request" if kind == "pr" else "issues"},
        },
    )
    return Event.model_validate(
        {**json.loads(rendered), "id": uuid4(), "occurred": datetime.now(UTC)}
    )


@pytest.mark.parametrize(
    "kwargs,accepted",
    [
        ({"draft": True}, False),
        (
            {"draft": True, "action": "edited", "changes": {"body": {"from": "old"}}},
            False,
        ),
        ({"body": "Refactor skill snapshots"}, False),
        ({"body": None}, False),
        ({"action": "edited", "changes": {"title": {"from": "old"}}}, False),
        ({"sender": "Bot"}, False),
        ({}, True),
        ({"action": "ready_for_review"}, True),
        ({"action": "synchronize"}, True),
        ({"action": "edited", "changes": {"body": {"from": "old"}}}, True),
        ({"body": "Fixes https://github.com/PrefectHQ/fastmcp/issues/123"}, True),
        (
            {
                "kind": "issue",
                "action": "edited",
                "changes": {"title": {"from": "old"}},
            },
            True,
        ),
    ],
)
def test_github_events_filter_before_launch(automations, kwargs, accepted):
    configs, _, _ = automations
    event = github_event(configs, **kwargs)
    kind = kwargs.get("kind", "pr")
    trigger = AutomationCore.model_validate(
        configs["fastmcp-triage-on-github-" + kind]
    ).trigger
    assert (trigger.covers(event) and trigger.expects(event.event)) is accepted


def test_launches_coalesce_per_github_item_and_fetch_after_the_window(automations):
    configs, _, _ = automations
    for kind in ("issue", "pr"):
        config = configs["fastmcp-triage-on-github-" + kind]
        trigger = AutomationCore.model_validate(config).trigger
        assert trigger.within.total_seconds() == 60
        assert trigger.for_each == {"prefect.resource.id"}
        assert config["actions"][0]["schedule_after"] == 60


@pytest.mark.parametrize("state", ["Failed", "Crashed", "TimedOut", "Degraded"])
def test_failure_alert_is_once_at_intake_not_each_child(automations, state):
    configs, controller, intake = automations
    trigger = AutomationCore.model_validate(
        configs["fastmcp-triage-failure-alert"]
    ).trigger
    for deployment, expected in [
        (intake, True),
        (controller, False),
        (str(uuid4()), False),
    ]:
        event = Event.model_validate(
            {
                "id": uuid4(),
                "occurred": datetime.now(UTC),
                "event": "prefect.flow-run." + state,
                "resource": {"prefect.resource.id": "prefect.flow-run." + str(uuid4())},
                "related": [
                    {
                        "prefect.resource.id": "prefect.deployment." + deployment,
                        "prefect.resource.role": "deployment",
                    }
                ],
            }
        )
        assert (trigger.covers(event) and trigger.expects(event.event)) is expected


def test_failure_alert_shows_the_cause_and_one_run_link(automations):
    configs, _, _ = automations
    register_user_template_filters(
        {"ui_url": lambda run: "https://app.prefect.cloud/runs/" + run["id"]}
    )
    message = render_user_template_sync(
        configs["fastmcp-triage-failure-alert"]["actions"][0]["body"],
        {
            "flow_run": {
                "id": "run",
                "state": {
                    "name": "Degraded",
                    "message": "#4866: contribution verification did not complete",
                },
            },
        },
    )
    assert "triage degraded" in message
    assert "#4866: contribution verification did not complete" in message
    assert message.count("https://app.prefect.cloud/runs/run") == 1


def test_notification_announces_admitted_issues_only(automations):
    configs, controller, intake = automations
    config = configs["fastmcp-triage-run-started"]
    trigger = AutomationCore.model_validate(config).trigger
    run_id = str(uuid4())

    def event(deployment):
        return Event.model_validate(
            {
                "id": uuid4(),
                "occurred": datetime.now(UTC),
                "event": "prefect.flow-run.Running",
                "resource": {
                    "prefect.resource.id": "prefect.flow-run." + run_id,
                    "prefect.resource.name": "fastmcp-stack-intake-" + run_id,
                },
                "related": [
                    {
                        "prefect.resource.id": "prefect.deployment." + deployment,
                        "prefect.resource.role": "deployment",
                    }
                ],
            }
        )

    assert not trigger.covers(event(intake))
    assert trigger.covers(event(controller))
    register_user_template_filters(
        {"ui_url": lambda run: "https://app.prefect.cloud/runs/" + run["id"]}
    )
    message = render_user_template_sync(
        config["actions"][0]["body"],
        {
            "flow_run": {
                "id": run_id,
                "parameters": {
                    "batch": {
                        "groups": [
                            {"items": [{"issue_number": 123}, {"issue_number": 456}]},
                        ]
                    }
                },
            },
        },
    )
    assert message == (
        "Accepted for triage: <https://github.com/PrefectHQ/fastmcp/issues/123|#123>, "
        "<https://github.com/PrefectHQ/fastmcp/issues/456|#456>\n"
        f"<https://app.prefect.cloud/runs/{run_id}|Follow triage>"
    )
