"""Session, Host, Origin, and isolation checks of the `fastmcp dev apps` host."""

import html
from typing import Any
from unittest.mock import AsyncMock

import httpx
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

    def test_session_cookie_does_not_hold_startup_token(self):
        client = build_client(session=True)

        session = client.cookies.get(COOKIE)

        assert session
        assert TOKEN not in session

    @pytest.mark.parametrize("token", ["wrong", "", "é"])
    def test_wrong_startup_token_starts_no_session(self, token: str):
        response = build_client().get("/", params={"token": token})

        assert response.status_code == 403
        assert "set-cookie" not in response.headers

    def test_websocket_connections_are_closed(self):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with build_client(session=True).websocket_connect("/mcp"):
                pass
        assert exc_info.value.code == 1008


class TestHostHeader:
    @pytest.mark.parametrize(
        "host",
        [
            "other.example:8080",
            "127.0.0.1.other.example:8080",
            "localhost.other.example:8080",
            "127.0.0.1:9999",
            "127.0.0.1",
            "[::1]:9999",
            "2130706433:8080",
            "127.0.0.1:8080@other.example:8080",
            "127.0.0.1:8080/path",
            "[invalid]:8080",
        ],
    )
    def test_other_hosts_are_rejected_before_session_start(self, host: str):
        response = build_client().get(
            "/", params={"token": TOKEN}, headers={"Host": host}
        )

        assert response.status_code == 400
        assert "set-cookie" not in response.headers

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
            ("other.example:8080", 400),
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
            {"Origin": "http://other.example"},
            {"Origin": "http://127.0.0.1:9999"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ],
    )
    def test_cross_origin_launch_page_is_rejected(self, headers: dict[str, str]):
        client = build_client(session=True)
        launch_url = create_launch(client)

        response = client.get(launch_url, headers=headers)

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "headers",
        [
            {"Origin": "http://other.example"},
            {"Origin": "http://127.0.0.1:9999"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ],
    )
    def test_cross_origin_log_clear_is_rejected(self, headers: dict[str, str]):
        client = build_client(session=True)

        response = client.post("/api/logs/clear", content="{}", headers=headers)

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

    def test_typed_launch_url_with_tool_arguments_runs_nothing(self):
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


class TestServerContent:
    def test_picker_error_is_escaped(self, monkeypatch: pytest.MonkeyPatch):
        markup = '<b class="tool">name</b>'
        monkeypatch.setattr(
            apps_dev, "_list_tools", AsyncMock(side_effect=ValueError(markup))
        )

        response = build_client(session=True).get("/picker-app")

        assert response.status_code == 200
        assert markup not in response.text
        assert html.escape(markup) in response.text

    def test_ui_resource_is_sandboxed_when_opened_directly(
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

    def test_host_pages_cannot_be_framed(self):
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

    def test_proxy_forwards_no_browser_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured: list[httpx.Request] = []
        original_client = httpx.AsyncClient

        def backend(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={
                    "Set-Cookie": f"{COOKIE}=other",
                    "Content-Security-Policy": "frame-ancestors *",
                },
            )

        def client_factory(**kwargs: Any) -> httpx.AsyncClient:
            return original_client(transport=httpx.MockTransport(backend), **kwargs)

        monkeypatch.setattr(apps_dev.httpx, "AsyncClient", client_factory)

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
