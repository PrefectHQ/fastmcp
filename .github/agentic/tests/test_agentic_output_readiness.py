import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.states import Running
from prefect.testing.utilities import prefect_test_harness

from flows import agentic_stack_runtime as runtime
from flows.agentic_stack_runtime import AgentPlan, judge


@flow
def output_owner():
    return {}


def test_completed_agent_waits_for_declared_output_without_restarting(monkeypatch):
    plan = AgentPlan(flow_id=uuid4(), version_id=uuid4())
    protocol = SimpleNamespace(
        starts=0, observations=0, output_reads=0, status="completed"
    )
    decision = {
        "decision": "not_reproduced",
        "base_sha": "a" * 40,
        "regression_patch": "",
        "command": "uv run pytest tests/test_fixture.py",
        "observed": "Fixture output returned after delayed materialization.",
        "contract": "",
        "source_paths": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            protocol.starts += 1
            self.respond({"id": str(uuid4())})

        def do_GET(self):
            if self.path.startswith("/flows/"):
                self.respond({"active_version": {"id": str(plan.version_id)}})
            elif self.path.endswith("/outputs/result"):
                protocol.output_reads += 1
                if protocol.observations < 3:
                    self.respond(
                        {"reason": "output_not_available", "output_status": "waiting"},
                        409,
                    )
                else:
                    self.respond({"decision": decision})
            else:
                protocol.observations += 1
                self.respond(
                    {
                        "status": protocol.status,
                        "snapshot": {"execution_plan_version_id": str(plan.version_id)},
                        "outputs": [
                            {
                                "output": "result",
                                "status": "available"
                                if protocol.observations >= 3
                                else "waiting",
                            }
                        ],
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as remote:

            def api(method, path, body=None):
                response = remote.request(method, path, json=body)
                response.raise_for_status()
                return response.json()

            monkeypatch.setattr(runtime, "api", api)
            monkeypatch.setattr(
                runtime, "time", SimpleNamespace(sleep=lambda seconds: None)
            )
            with prefect_test_harness(), get_client(sync_client=True) as client:
                owner = client.create_flow_run(output_owner, state=Running())
                deadline = datetime.now(UTC) + timedelta(minutes=1)
                result, run_id = judge(
                    "reproduce", plan, {}, {}, owner.id, deadline, "readiness"
                )
                assert result == decision
                assert run_id
                assert protocol.observations == 3
                assert protocol.starts == 1
                assert protocol.output_reads == 1
                assert judge(
                    "reproduce", plan, {}, {}, owner.id, deadline, "readiness"
                ) == (result, run_id)
                assert protocol.starts == 1

                protocol.status = "failed"
                with pytest.raises(RuntimeError, match="ended failed"):
                    judge("reproduce", plan, {}, {}, owner.id, deadline, "failed-agent")
                assert protocol.output_reads == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
