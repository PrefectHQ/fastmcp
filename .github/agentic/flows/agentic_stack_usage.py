from __future__ import annotations

import re
from uuid import UUID

from prefect import task
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter, LogFilter
from prefect.client.schemas.sorting import LogSort


@task(
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
    persist_result=False,
)
def usage_report(owner: UUID) -> dict:
    runs: list[dict] = []
    with get_client(sync_client=True) as client:
        records = client.read_artifacts(
            artifact_filter=ArtifactFilter(type={"any_": ["fastmcp-stack-controller"]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner]}),
            limit=200,
        )
        if len(records) == 200:
            raise ValueError("Controller record scan exceeded its bound")
        admissions = {
            record.data["run_id"]: record.data
            for record in records
            if record.key
            and record.key.endswith("-agent-run")
            and isinstance(record.data, dict)
        }
        for run_id, admission in sorted(admissions.items()):
            if admission.get("reused") is True:
                runs.append(
                    {
                        **admission,
                        "observed_requests": 0,
                        "requests_missing_usage": 0,
                        "log_scan_exhausted": True,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "basis": "Reused a completed assessment; its historical model usage is excluded from this batch.",
                    }
                )
                continue
            seen = set()
            counters = {"input_tokens": 0, "output_tokens": 0}
            observed = 0
            missing = 0
            exhausted = False
            for page in range(10):
                logs = client.read_logs(
                    log_filter=LogFilter(flow_run_id={"any_": [UUID(run_id)]}),
                    limit=200,
                    offset=page * 200,
                    sort=LogSort.TIMESTAMP_ASC,
                )
                for log in logs:
                    if log.id in seen:
                        continue
                    seen.add(log.id)
                    if log.name != "prefect.agent.activity" or not re.match(
                        r"^\[attempt \d+\] Model request (completed|failed|retried) in ",
                        log.message,
                    ):
                        continue
                    counts = {
                        key: int(value)
                        for key, value in re.findall(
                            r"\b(input_tokens|output_tokens)=(\d+)\b", log.message
                        )
                    }
                    if counts.keys() != counters.keys():
                        missing += 1
                        continue
                    observed += 1
                    for key, value in counts.items():
                        counters[key] += value
                if len(logs) < 200:
                    exhausted = True
                    break
            runs.append(
                {
                    **admission,
                    "observed_requests": observed,
                    "requests_missing_usage": missing,
                    "log_scan_exhausted": exhausted,
                    **{
                        key: value if observed else None
                        for key, value in counters.items()
                    },
                }
            )
    return {
        "status": "observed_partial" if runs else "no_recorded_agent_runs",
        "basis": "Best-effort projected model logs; missing or delayed telemetry is unknown.",
        "runs": runs,
        **{
            key: sum(run[key] or 0 for run in runs)
            if any(run[key] is not None for run in runs)
            else None
            for key in ("input_tokens", "output_tokens")
        },
        "cost": None,
        "cost_basis": "Provider billing unavailable; tokens are not a dollar estimate.",
    }
