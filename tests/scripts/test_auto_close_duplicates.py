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
