import httpx
import pytest

from scripts import auto_close_duplicates as duplicate


@pytest.mark.parametrize(
    ("endpoint", "scenario", "writes"),
    [
        ("comments", "http_error", []),
        ("reactions", "http_error", []),
        ("comments", "page_limit", []),
        ("reactions", "page_limit", []),
        ("reactions", "objected", ["DELETE"]),
        ("reactions", "unchallenged", ["POST", "DELETE", "POST", "PATCH"]),
    ],
)
def test_duplicate_close_uses_complete_evidence(
    endpoint: str, scenario: str, writes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.Client
    calls: list[str] = []
    issue = {
        "number": 1,
        "title": "A duplicate report",
        "state": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "user": {"id": 1, "login": "reporter"},
    }
    warning = {
        "id": 2,
        "body": "Found 1 possible duplicate issue",
        "created_at": "2026-01-01T00:00:00Z",
        "user": {"id": 2, "login": "marvin[bot]", "type": "Bot"},
    }

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        path = request.url.path
        if request.method != "GET":
            status = 201 if path.endswith("/comments") else 200
            return httpx.Response(status)
        if path.endswith("/issues"):
            return httpx.Response(
                200, json=[issue] if request.url.params["page"] == "1" else []
            )
        result = [warning] if path.endswith("/comments") else []
        if path.endswith("/reactions"):
            assert path.endswith("/issues/comments/2/reactions")
        if path.endswith(f"/{endpoint}"):
            if scenario == "http_error":
                return httpx.Response(503)
            if scenario == "page_limit" or (
                scenario == "objected" and request.url.params["page"] == "1"
            ):
                return httpx.Response(
                    200, json=result, headers={"Link": f'<{request.url}>; rel="next"'}
                )
            if scenario == "objected":
                result = [{"user": {"id": 1, "login": "reporter"}, "content": "-1"}]
        return httpx.Response(200, json=result)

    transport = httpx.MockTransport(respond)
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setattr(httpx, "Client", lambda: real_client(transport=transport))

    if scenario in {"http_error", "page_limit"}:
        with pytest.raises(SystemExit) as stopped:
            duplicate.main()
        assert stopped.value.code == 1
    else:
        duplicate.main()

    assert [method for method in calls if method != "GET"] == writes


@pytest.mark.parametrize(
    ("association", "scenario", "expected_requests"),
    [
        ("CONTRIBUTOR", "followup", ["timeline", "issue", "reopen"]),
        ("MEMBER", "followup", ["timeline", "issue", "reopen"]),
        ("NONE", "reporter", ["timeline", "issue", "reopen"]),
        ("NONE", "followup", []),
        ("CONTRIBUTOR", "bot", []),
        ("CONTRIBUTOR", "before_close", []),
        ("CONTRIBUTOR", "pull_request", []),
        ("CONTRIBUTOR", "manual_close", ["timeline"]),
        ("CONTRIBUTOR", "previous_closure", ["timeline"]),
        ("CONTRIBUTOR", "already_reopened", ["timeline", "issue"]),
        ("CONTRIBUTOR", "closed_again", ["timeline", "issue"]),
        ("CONTRIBUTOR", "label_removed", ["timeline", "issue"]),
    ],
)
@pytest.mark.parametrize(
    "closer",
    [duplicate.MARVIN_BOT_ID, duplicate.ACTIONS_BOT_ID],
    ids=["marvin", "actions"],
)
def test_duplicate_followup_reopens_only_current_bot_closure(
    association: str, scenario: str, expected_requests: list[str], closer: int
) -> None:
    closed_at = "2026-01-01T00:00:04Z"
    issue = {
        "number": 1,
        "state": "closed",
        "labels": [{"name": "duplicate"}],
        "user": {"id": 3},
        "closed_at": closed_at,
    }
    event = {
        "issue": {
            **issue,
            **({"pull_request": {}} if scenario == "pull_request" else {}),
        },
        "comment": {
            "id": 5,
            "user": {
                "id": 3 if scenario == "reporter" else 4,
                "type": "Bot" if scenario == "bot" else "User",
            },
            "author_association": association,
            "created_at": (
                "2026-01-01T00:00:03Z" if scenario == "before_close" else closed_at
            ),
        },
    }
    timeline = [
        {
            "event": "commented",
            "user": {"id": closer},
            "body": duplicate.CLOSURE_NOTICE,
            "created_at": "2026-01-01T00:00:02Z",
        },
        {
            "event": "closed",
            "actor": {"id": 4 if scenario == "manual_close" else closer},
            "created_at": closed_at,
        },
    ]
    if scenario == "previous_closure":
        timeline.insert(1, {"event": "reopened", "created_at": "2026-01-01T00:00:03Z"})
    if scenario == "already_reopened":
        issue["state"] = "open"
    if scenario == "closed_again":
        issue["closed_at"] = "2026-01-01T00:01:00Z"
    if scenario == "label_removed":
        issue["labels"] = []

    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/1/timeline"):
            assert request.method == "GET"
            calls.append("timeline")
            return httpx.Response(200, json=timeline)
        assert request.url.path.endswith("/issues/1")
        if request.method == "GET":
            calls.append("issue")
            return httpx.Response(200, json=issue)
        assert request.method == "PATCH"
        assert request.content == b'{"state":"open"}'
        calls.append("reopen")
        return httpx.Response(200, json={"state": "open"})

    real_client = httpx.Client
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            httpx, "Client", lambda: real_client(transport=httpx.MockTransport(respond))
        )
        duplicate.reconsider_duplicate(
            duplicate.GitHubClient("test", "owner", "repo"), event
        )

    assert calls == expected_requests
