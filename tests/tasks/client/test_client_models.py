from fastmcp_tasks.client_models import ClientCreateTaskResult


def test_client_task_timing_fields_parse_as_integers():
    result = ClientCreateTaskResult.model_validate(
        {
            "taskId": "t1",
            "status": "working",
            "createdAt": "2026-09-03T00:00:00Z",
            "lastUpdatedAt": "2026-09-03T00:00:00Z",
            "ttlMs": 900000,
            "pollIntervalMs": 5000,
            "resultType": "task",
        },
        by_name=False,
    )

    assert type(result.ttl_ms) is int
    assert type(result.poll_interval_ms) is int
