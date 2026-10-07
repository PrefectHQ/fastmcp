"""Sessions, launches, and app frames in the `fastmcp dev apps` server."""

import html
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import fastmcp.cli.apps_dev as apps_dev

TOKEN = "test-process-session-token"
COOKIE = "fastmcp_dev_session_8080"


def build_client(*, host: str = "127.0.0.1", session: bool = False) -> TestClient:
    app = apps_dev._make_dev_app(
        "http://127.0.0.1:1/mcp",
        "",
        "",
        apps_dev._MessageLog(),
        False,
        host=host,
        port=8080,
        session_token=TOKEN,
    )
    client = TestClient(app, base_url="http://127.0.0.1:8080")
    if session:
        client.get("/", params={"token": TOKEN})
    return client


def create_launch(client: TestClient, tool: str = "ping") -> str:
    response = client.post("/api/launch", json={"tool": tool, "value": "x"})
    return response.json()


class TestSession:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/"),
            ("get", "/launch?id=x"),
            ("get", "/picker-app"),
            ("get", "/ui-resource?uri=ui://app/view.html"),
            ("get", "/js/app-bridge.js"),
            ("get", "/api/logs"),
            ("post", "/api/logs/bridge"),
            ("post", "/api/logs/clear"),
            ("post", "/api/launch"),
            ("get", "/mcp"),
            ("post", "/mcp"),
            ("delete", "/mcp"),
        ],
    )
    def test_requests_without_session_are_rejected(self, method: str, path: str):
        response = build_client().request(method, path)
        assert response.status_code == 403

    def test_startup_url_starts_session_and_redirects(self):
        client = build_client()

        response = client.get("/", params={"token": TOKEN}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE}=")
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/").status_code == 200
        assert client.get(create_launch(client)).status_code == 200

    def test_session_cookie_differs_from_startup_token(self):
        client = build_client(session=True)

        session = client.cookies.get(COOKIE)

        assert session
        assert TOKEN not in session

    @pytest.mark.parametrize("token", ["wrong", "", "é"])
    def test_wrong_startup_token_starts_no_session(self, token: str):
        response = build_client().get("/", params={"token": token})

        assert response.status_code == 403
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "token",
        [
            TOKEN.upper(),
            TOKEN[:-1],
            TOKEN + "x",
            f" {TOKEN}",
            f"{TOKEN} ",
            "名前",
            "wrong",
            "",
        ],
        ids=[
            "uppercase",
            "truncated",
            "extended",
            "leading_space",
            "trailing_space",
            "non_ascii",
            "wrong",
            "empty",
        ],
    )
    def test_startup_token_must_match_exactly(self, token: str):
        client = build_client()

        response = client.get("/", params={"token": token})

        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert client.get("/").status_code == 403

    @pytest.mark.parametrize(
        "method,path",
        [("get", "/picker-app"), ("get", "/api/logs"), ("post", "/api/launch")],
    )
    def test_startup_token_only_starts_a_session_from_the_root_page(
        self, method: str, path: str
    ):
        client = build_client()

        response = client.request(method, path, params={"token": TOKEN})

        assert response.status_code == 403
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "cookie",
        [
            f"{COOKIE}=wrong",
            f"{COOKIE}=",
            f"{COOKIE}={TOKEN}",
            "fastmcp_dev_session_9999=anything",
            "fastmcp_dev_session=anything",
        ],
        ids=["wrong", "empty", "startup_token", "other_port", "no_port"],
    )
    def test_other_cookie_values_are_not_a_session(self, cookie: str):
        response = build_client().get("/", headers={"Cookie": cookie})

        assert response.status_code == 403

    def test_websocket_connections_are_closed(self):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with build_client(session=True).websocket_connect("/mcp"):
                pass
        assert exc_info.value.code == 1008


class TestHostHeader:
    @pytest.mark.parametrize(
        "host",
        [
            "example.com:8080",
            "dev.example.com:8080",
            "127.0.0.1:9000",
            "127.0.0.1",
            "[::1]:9000",
            "192.0.2.2:8080",
        ],
    )
    def test_other_host_headers_are_rejected(self, host: str):
        response = build_client().get(
            "/", params={"token": TOKEN}, headers={"Host": host}
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "host",
        [
            "other.example:8080",
            "127.0.0.1.other.example:8080",
            "localhost.other.example:8080",
            "127.0.0.1:9999",
            "127.0.0.1",
            "127.0.0.1:",
            "127.0.0.1:99999",
            "127.0.0.1:http",
            "[::1]:9999",
            "[::1]",
            "127.0.0.1:8080@other.example:8080",
            "other.example:8080@127.0.0.1:8080",
            "127.0.0.1:8080/path",
            "127.0.0.1:8080?name=value",
            "127.0.0.1:8080#fragment",
            "[invalid]:8080",
            "2130706433:8080",
            "127.1:8080",
            "0x7f.0.0.1:8080",
            "[::ffff:127.0.0.1]:8080",
            "localhost.:8080",
            "127.0.0.1.:8080",
            "localhost:8080:8080",
            "127.0.0.1:8080:8080",
        ],
    )
    def test_malformed_or_mismatched_host_values_are_rejected(self, host: str):
        response = build_client().get(
            "/", params={"token": TOKEN}, headers={"Host": host}
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize("host", ["LOCALHOST:8080", "Localhost:8080"])
    def test_host_names_are_case_insensitive(self, host: str):
        response = build_client().get(
            "/", params={"token": TOKEN}, headers={"Host": host}, follow_redirects=False
        )

        assert response.status_code == 303

    def test_bound_host_name_is_case_insensitive(self):
        response = build_client(host="Dev.Example").get(
            "/",
            params={"token": TOKEN},
            headers={"Host": "DEV.example:8080"},
            follow_redirects=False,
        )

        assert response.status_code == 303

    @pytest.mark.parametrize(
        "second_host", ["127.0.0.1:8080", "other.example:8080"], ids=["same", "other"]
    )
    def test_multiple_host_headers_are_rejected(self, second_host: str):
        response = build_client().get(
            "/",
            params={"token": TOKEN},
            headers=[("Host", "127.0.0.1:8080"), ("Host", second_host)],
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "host",
        [
            "other.example:8080",
            "localhost.other.example:8080",
            "192.0.2.2:9999",
            "192.0.2.2",
            "192.0.2.20:8080",
            "192.0.2.2.other.example:8080",
            "192.0.2.2:8080@other.example:8080",
            "localhost:8080",
            "[::1]:8080",
        ],
    )
    def test_specific_bind_rejects_other_host_values(self, host: str):
        response = build_client(host="192.0.2.2").get(
            "/", params={"token": TOKEN}, headers={"Host": host}
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "host",
        [
            "other.example:8080",
            "localhost.other.example:8080",
            "192.0.2.2:9999",
            "192.0.2.2",
            "2130706433:8080",
            "127.1:8080",
            "0x7f.0.0.1:8080",
            "localhost.:8080",
            "192.0.2.2:8080@other.example:8080",
            "192.0.2.2:8080/path",
        ],
    )
    def test_wildcard_bind_rejects_non_literal_and_malformed_hosts(self, host: str):
        response = build_client(host="0.0.0.0").get(
            "/", params={"token": TOKEN}, headers={"Host": host}
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize(
        "host", ["localhost:8080", "[::1]:8080", "[2001:db8::1]:8080"]
    )
    def test_wildcard_bind_accepts_loopback_names_and_ipv6_literals(self, host: str):
        response = build_client(host="::").get(
            "/", params={"token": TOKEN}, headers={"Host": host}, follow_redirects=False
        )

        assert response.status_code == 303

    def test_default_http_port_accepts_host_without_port(self):
        app = apps_dev._make_dev_app(
            "http://127.0.0.1:1/mcp",
            "",
            "",
            apps_dev._MessageLog(),
            False,
            port=80,
            session_token=TOKEN,
        )
        client = TestClient(app, base_url="http://127.0.0.1")

        startup = client.get("/", params={"token": TOKEN}, follow_redirects=False)
        page = client.get("/", headers={"Origin": "http://127.0.0.1"})

        assert startup.status_code == 303
        assert page.status_code == 200

    @pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "[::1]:8080"])
    def test_loopback_names_are_accepted(self, host: str):
        response = build_client().get(
            "/", params={"token": TOKEN}, headers={"Host": host}, follow_redirects=False
        )
        assert response.status_code == 303

    def test_specific_bind_accepts_only_its_host(self):
        client = build_client(host="192.0.2.2")
        startup = {"token": TOKEN}

        own = client.get(
            "/",
            params=startup,
            headers={"Host": "192.0.2.2:8080"},
            follow_redirects=False,
        )
        other = client.get("/", params=startup, headers={"Host": "127.0.0.1:8080"})

        assert own.status_code == 303
        assert other.status_code == 400

    @pytest.mark.parametrize(
        "host,status",
        [
            ("192.0.2.2:8080", 303),
            ("127.0.0.1:8080", 303),
            ("example.com:8080", 400),
        ],
    )
    def test_wildcard_bind_accepts_literal_addresses(self, host: str, status: int):
        response = build_client(host="0.0.0.0").get(
            "/", params={"token": TOKEN}, headers={"Host": host}, follow_redirects=False
        )
        assert response.status_code == status


class TestOrigin:
    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "http://example.com"},
            {"Origin": "http://127.0.0.1:9000"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ],
    )
    def test_launch_page_requires_same_origin(self, headers: dict[str, str]):
        client = build_client(session=True)
        launch_url = create_launch(client)

        response = client.get(launch_url, headers=headers)

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "http://example.com"},
            {"Origin": "http://127.0.0.1:9000"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ],
    )
    def test_log_clear_requires_same_origin(self, headers: dict[str, str]):
        client = build_client(session=True)

        response = client.post("/api/logs/clear", content="{}", headers=headers)

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "http://other.example"},
            {"Origin": "http://127.0.0.1:9999"},
            {"Origin": "http://localhost:8080"},
            {"Origin": "http://127.0.0.1"},
            {"Origin": "https://127.0.0.1:8080"},
            {"Origin": "HTTP://127.0.0.1:8080"},
            {"Origin": "http://127.0.0.1:8080/"},
            {"Origin": "http://127.0.0.1:8080@other.example"},
            {"Origin": "http://other.example@127.0.0.1:8080"},
            {"Origin": "http://127.0.0.1.other.example:8080"},
            {"Origin": ""},
            {"Origin": "null"},
            {"Origin": "http://127.0.0.1:8080", "Sec-Fetch-Site": "cross-site"},
            {"Origin": "http://127.0.0.1:8080", "Sec-Fetch-Site": "same-site"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"Sec-Fetch-Site": "Cross-Site"},
            {"Sec-Fetch-Site": ""},
            {"Sec-Fetch-Site": "same-origin, cross-site"},
        ],
    )
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/"),
            ("get", "/picker-app"),
            ("get", "/api/logs"),
            ("post", "/api/launch"),
            ("post", "/api/logs/clear"),
            ("post", "/mcp"),
        ],
    )
    def test_cross_origin_requests_are_rejected_on_every_route(
        self, method: str, path: str, headers: dict[str, str]
    ):
        response = build_client(session=True).request(method, path, headers=headers)

        assert response.status_code == 403

    def test_referer_does_not_replace_the_origin_check(self):
        response = build_client(session=True).post(
            "/api/launch",
            json={"tool": "ping", "value": "x"},
            headers={
                "Origin": "http://other.example",
                "Referer": "http://127.0.0.1:8080/",
            },
        )

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "http://127.0.0.1:8080"},
            {"Sec-Fetch-Site": "same-origin"},
            {"Sec-Fetch-Site": "none"},
            {},
        ],
    )
    def test_same_origin_requests_work(self, headers: dict[str, str]):
        response = build_client(session=True).post(
            "/api/launch", json={"tool": "ping", "value": "x"}, headers=headers
        )

        assert response.status_code == 200
        assert response.json().startswith("/launch?id=")

    def test_log_endpoints_work_for_the_session(self):
        client = build_client(session=True)

        client.post("/api/logs/bridge", json={"body": {"method": "legitimate"}})
        logged = client.get("/api/logs").text
        client.post("/api/logs/clear")

        assert "legitimate" in logged
        assert "legitimate" not in client.get("/api/logs").text


class TestLaunch:
    def test_launch_url_from_picker_opens_launch_page(self):
        client = build_client(session=True)

        response = client.get(create_launch(client, tool="lookup"))

        assert response.status_code == 200
        assert 'const toolName = "lookup";' in response.text

    def test_launch_ignores_tool_and_args_parameters(self):
        client = build_client(session=True)

        response = client.get(
            "/launch",
            params={"tool": "ping", "args": '{"x": 1}'},
            headers={"Sec-Fetch-Site": "none"},
        )

        assert response.status_code == 404
        assert "toolName" not in response.text

    def test_unknown_launch_id_is_rejected(self):
        response = build_client(session=True).get("/launch", params={"id": "unknown"})

        assert response.status_code == 404


class TestPages:
    def test_picker_error_shows_message_as_text(self, monkeypatch: pytest.MonkeyPatch):
        message = "Expected <list> of items"
        monkeypatch.setattr(
            apps_dev, "_list_tools", AsyncMock(side_effect=ValueError(message))
        )

        response = build_client(session=True).get("/picker-app")

        assert response.status_code == 200
        assert message not in response.text
        assert html.escape(message) in response.text

    def test_ui_resource_runs_sandboxed_when_opened_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            apps_dev, "_read_mcp_resource", AsyncMock(return_value="<p>app</p>")
        )

        response = build_client(session=True).get("/ui-resource?uri=ui://app/view.html")

        assert response.status_code == 200
        csp = response.headers["content-security-policy"]
        assert "sandbox allow-scripts allow-forms" in csp
        assert "allow-same-origin" not in csp
        assert "frame-ancestors 'self'" in csp

    def test_host_pages_disallow_framing(self):
        response = build_client(session=True).get("/")
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'"

    def test_launch_page_sandboxes_app_frame(self):
        client = build_client(session=True)
        page = client.get(create_launch(client)).text

        assert '<iframe id="app-frame" sandbox="allow-scripts allow-forms">' in page
        assert "new PostMessageTransport(iframe.contentWindow, null)" not in page
        assert "contentDocument" not in page

    def test_launch_page_opens_only_external_web_links(self):
        client = build_client(session=True)
        page = client.get(create_launch(client)).text

        assert 'target.protocol !== "https:"' in page
        assert "target.origin === window.location.origin" in page

    def test_picker_page_opens_only_web_links(self):
        page = build_client(session=True).get("/").text

        assert 'target.protocol !== "https:"' in page
        assert "window.location.href = url;" not in page

    def test_proxy_omits_browser_cookies_and_origin(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured: list[httpx2.Request] = []
        original_client = httpx2.AsyncClient

        def backend(request: httpx2.Request) -> httpx2.Response:
            captured.append(request)
            return httpx2.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={
                    "Set-Cookie": f"{COOKIE}=other",
                    "Content-Security-Policy": "frame-ancestors *",
                },
            )

        def client_factory(**kwargs: Any) -> httpx2.AsyncClient:
            return original_client(transport=httpx2.MockTransport(backend), **kwargs)

        monkeypatch.setattr(apps_dev.httpx2, "AsyncClient", client_factory)

        response = build_client(session=True).post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Origin": "http://127.0.0.1:8080",
                "Referer": "http://127.0.0.1:8080/",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200
        forwarded = {key.lower() for key in captured[0].headers}
        assert forwarded.isdisjoint({"cookie", "origin", "referer", "sec-fetch-site"})
        assert "set-cookie" not in response.headers
        assert response.headers["content-security-policy"] == (
            "frame-ancestors 'none'; sandbox allow-scripts allow-forms"
        )


class TestPickerForm:
    @pytest.mark.parametrize(
        "name,field_name",
        [
            ("__base__", "field_base_"),
            ("__config__", "field_config_"),
            ("model_config", "field_model_config"),
            ("_private", "field_private"),
            ("plain", "plain"),
        ],
    )
    def test_form_submits_original_property_names(self, name: str, field_name: str):
        schema = {
            "type": "object",
            "properties": {name: {"type": "string"}},
            "required": [name],
        }

        model = apps_dev._model_from_schema("probe", schema)
        page = apps_dev._build_picker_html(
            [
                {
                    "name": "probe",
                    "inputSchema": schema,
                    "_meta": {"ui": {"resourceUri": "ui://probe/view.html"}},
                }
            ]
        )

        assert list(model.model_fields) == [field_name]
        assert model.model_validate({name: "value"}).model_dump(by_alias=True) == {
            name: "value"
        }
        assert f'"{name}":"{{{{ {field_name} }}}}"' in page


class TestSpawnedServer:
    async def test_host_origin_protection_defaults_to_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", raising=False)
        spawn = AsyncMock()
        monkeypatch.setattr(apps_dev.asyncio, "create_subprocess_exec", spawn)

        await apps_dev._start_user_server("server.py", 8000, reload=False)

        env = spawn.call_args.kwargs["env"]
        assert env["FASTMCP_HTTP_HOST_ORIGIN_PROTECTION"] == "auto"

    async def test_explicit_host_origin_protection_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("FASTMCP_HTTP_HOST_ORIGIN_PROTECTION", "true")
        spawn = AsyncMock()
        monkeypatch.setattr(apps_dev.asyncio, "create_subprocess_exec", spawn)

        await apps_dev._start_user_server("server.py", 8000, reload=False)

        env = spawn.call_args.kwargs["env"]
        assert env["FASTMCP_HTTP_HOST_ORIGIN_PROTECTION"] == "true"


class TestUiToolDetection:
    @pytest.mark.parametrize(
        "tool",
        [
            {"name": "a", "_meta": {"ui": {"resourceUri": "ui://x/view.html"}}},
            {"name": "a", "meta": {"ui": {"resourceUri": "ui://x/view.html"}}},
            {"name": "a", "_meta": {"ui": {"resource_uri": "ui://x/view.html"}}},
        ],
    )
    def test_ui_tools_are_recognized(self, tool: dict):
        assert apps_dev._has_ui_resource(tool) is True

    @pytest.mark.parametrize(
        "tool",
        [
            {"name": "a"},
            {"name": "a", "_meta": {}},
            {"name": "a", "_meta": {"ui": {"visibility": ["app"]}}},
            {"name": "a", "_meta": {"ui": True}},
        ],
    )
    def test_non_ui_tools_are_rejected(self, tool: dict):
        assert apps_dev._has_ui_resource(tool) is False

    async def test_fastmcp_app_ui_tool_is_recognized(self):
        from fastmcp import FastMCP, FastMCPApp

        app = FastMCPApp("PickerProbe")

        @app.ui()
        def entry_point() -> str:
            return "hi"

        @app.tool()
        def backend_tool(value: str) -> str:
            return value

        server = FastMCP("Probe", providers=[app])
        tools = server._rewrite_prefab_uris(list(await server._list_tools()))
        wire = [
            t.to_mcp_tool().model_dump(by_alias=True)
            for t in tools
            if t.name == "entry_point"
        ]

        assert len(wire) == 1
        assert apps_dev._has_ui_resource(wire[0]) is True
        assert "No UI tools found" not in apps_dev._build_picker_html(wire)


class TestListToolsSdkTolerance:
    async def test_list_tools_accepts_extra_stream_items(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import contextlib

        import mcp.client.streamable_http as streamable_http_module
        import mcp as mcp_module

        @contextlib.asynccontextmanager
        async def fake_streams(url: str):
            # Newer SDKs may yield extra items (e.g. get_session_id).
            yield ("read", "write", lambda: "session-id")

        class FakeTool:
            def model_dump(self, *, by_alias: bool = False) -> dict:
                return {
                    "_meta": {"ui": {"resourceUri": "ui://probe/view.html"}},
                    "name": "probe",
                }

        class FakeResult:
            tools = [FakeTool()]

        class FakeSession:
            def __init__(self, read: object, write: object) -> None:
                assert (read, write) == ("read", "write")

            async def __aenter__(self) -> "FakeSession":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def initialize(self) -> None:
                return None

            async def list_tools(self) -> FakeResult:
                return FakeResult()

        monkeypatch.setattr(
            streamable_http_module, "streamable_http_client", fake_streams
        )
        monkeypatch.setattr(mcp_module, "ClientSession", FakeSession)

        tools = await apps_dev._list_tools("http://127.0.0.1:1/mcp")

        assert [t["name"] for t in tools] == ["probe"]
        assert apps_dev._has_ui_resource(tools[0]) is True
