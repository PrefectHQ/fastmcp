import json
from collections.abc import Callable
from unittest.mock import Mock
from urllib.parse import parse_qs

import httpx2
import pytest
from pydantic import SecretStr

import fastmcp
import fastmcp.cli.deploy.authentication as authentication_module
import fastmcp.cli.deploy.command as command_module
from fastmcp.cli.deploy.command import login, logout, whoami
from fastmcp.cli.deploy.credentials import CredentialStore
from fastmcp.cli.deploy.horizon_client import HorizonClient


class HorizonAuthAPI:
    def __init__(
        self,
        *,
        token_error: str | None = None,
        revoke_status: int = 204,
        invalid_api_key: str | None = None,
    ) -> None:
        self.token_error = token_error
        self.revoke_status = revoke_status
        self.invalid_api_key = invalid_api_key
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v0/oauth/device/authorization":
            return httpx2.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://horizon.prefect.io/oauth/device",
                    "verification_uri_complete": (
                        "https://horizon.prefect.io/oauth/device?user_code=ABCD-EFGH"
                    ),
                    "expires_in": 600,
                    "interval": 1,
                },
            )
        if path == "/api/v0/oauth/device/token":
            if self.token_error is not None:
                return httpx2.Response(400, json={"error": self.token_error})
            return httpx2.Response(
                200,
                json={"access_token": "fmcp_device_key", "token_type": "Bearer"},
            )
        if path == "/api/v0/me":
            if request.headers.get("Authorization") == (
                f"Bearer {self.invalid_api_key}"
            ):
                return httpx2.Response(401)
            return httpx2.Response(
                200,
                json={
                    "user": {
                        "id": "user-1",
                        "email": "ada@example.com",
                        "name": "Ada",
                    }
                },
            )
        if path == "/api/v0/me/api-key":
            return httpx2.Response(self.revoke_status)
        raise AssertionError(f"Unexpected request: {request.method} {path}")


@pytest.fixture
def use_horizon_api(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[HorizonAuthAPI], None]:
    def use(api: HorizonAuthAPI) -> None:
        transport = httpx2.MockTransport(api)

        def client(
            api_origin: str,
            *,
            api_key: SecretStr | str | None = None,
        ) -> HorizonClient:
            return HorizonClient(
                api_origin,
                api_key=api_key,
                transport=transport,
            )

        monkeypatch.setattr(command_module, "HorizonClient", client)

    return use


@pytest.fixture(autouse=True)
def no_device_poll_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_: float) -> None:
        return None

    monkeypatch.setattr(authentication_module.asyncio, "sleep", sleep)


async def test_json_login_writes_one_result_and_challenge_to_stderr(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = HorizonAuthAPI()
    use_horizon_api(api)
    browser_open = Mock()
    monkeypatch.setattr(command_module.webbrowser, "open", browser_open)
    monkeypatch.setattr(command_module.platform, "node", lambda: "Avery's laptop")
    monkeypatch.setattr(command_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(command_module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(command_module.fastmcp, "__version__", "4.0.0")

    await login(json_output=True)

    captured = capsys.readouterr()
    stdout_lines = captured.out.strip().splitlines()
    assert len(stdout_lines) == 1
    assert json.loads(stdout_lines[0]) == {
        "ok": True,
        "command": "login",
        "user": {
            "id": "user-1",
            "email": "ada@example.com",
            "name": "Ada",
        },
    }
    assert json.loads(captured.err) == {
        "event": "device_authorization",
        "verificationUrl": "https://horizon.prefect.io/oauth/device",
        "verificationUrlComplete": (
            "https://horizon.prefect.io/oauth/device?user_code=ABCD-EFGH"
        ),
        "userCode": "ABCD-EFGH",
    }
    browser_open.assert_not_called()
    authorization_request = next(
        request
        for request in api.requests
        if request.url.path == "/api/v0/oauth/device/authorization"
    )
    assert parse_qs(authorization_request.content.decode()) == {
        "client_id": ["fastmcp-cli"],
        "device_name": ["Avery's laptop"],
        "platform": ["darwin"],
        "architecture": ["arm64"],
        "client_version": ["4.0.0"],
    }

    state = json.loads(CredentialStore().path.read_text())
    assert state == {"schemaVersion": 1, "apiKey": "fmcp_device_key"}
    assert not (fastmcp.settings.home / "cli" / "config.json").exists()


async def test_login_host_is_saved_before_device_authorization(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = HorizonAuthAPI()
    use_horizon_api(api)

    await login(host="https://dev.horizon.prefect.io/", json_output=True)

    assert json.loads(capsys.readouterr().out)["ok"] is True
    configuration_path = fastmcp.settings.home / "cli" / "config.json"
    assert json.loads(configuration_path.read_text()) == {
        "schemaVersion": 1,
        "apiOrigin": "https://dev.horizon.prefect.io",
    }
    assert {request.url.host for request in api.requests} == {"dev.horizon.prefect.io"}


async def test_login_rejects_an_invalid_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="1"):
        await login(host="https://horizon.prefect.io/path", json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result["error"]["category"] == "invalid_host"
    assert CredentialStore().path.exists() is False


async def test_tty_login_survives_browser_open_failure(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_horizon_api(HorizonAuthAPI())
    browser_open = Mock(side_effect=OSError("No browser"))
    monkeypatch.setattr(command_module, "_can_open_browser", lambda: True)
    monkeypatch.setattr(command_module.webbrowser, "open", browser_open)

    await login()

    output = capsys.readouterr().out
    assert "https://horizon.prefect.io/oauth/device" in output
    assert "ABCD-EFGH" in output
    assert "Logged into Horizon" in output
    assert "Ada" in output
    assert "ada@example.com" in output
    assert "Organization" not in output
    browser_open.assert_called_once()


async def test_whoami_uses_the_stored_key_after_a_restart(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = HorizonAuthAPI()
    use_horizon_api(api)
    await login(json_output=True)
    capsys.readouterr()

    await whoami(json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "whoami"
    assert result["user"]["email"] == "ada@example.com"
    assert [request.url.path for request in api.requests].count("/api/v0/me") == 2


async def test_login_replaces_an_invalid_stored_key(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_horizon_api(HorizonAuthAPI(invalid_api_key="fmcp_stale_key"))
    CredentialStore().save("fmcp_stale_key")

    await login(json_output=True)

    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is True
    assert json.loads(captured.err)["event"] == "device_authorization"
    stored_key = CredentialStore().load()
    assert stored_key is not None
    assert stored_key.get_secret_value() == "fmcp_device_key"


async def test_login_never_persists_an_environment_key(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = HorizonAuthAPI()
    use_horizon_api(api)
    monkeypatch.setenv("HORIZON_API_KEY", "fmcp_environment_key")

    await login(json_output=True)

    assert CredentialStore().path.exists() is False
    assert not any(
        request.url.path.startswith("/api/v0/oauth/device") for request in api.requests
    )


async def test_json_whoami_does_not_start_device_authorization(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_open = Mock()
    monkeypatch.setattr(command_module.webbrowser, "open", browser_open)

    with pytest.raises(SystemExit, match="1"):
        await whoami(json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result["error"]["category"] == "authentication_required"
    browser_open.assert_not_called()


@pytest.mark.parametrize(
    ("token_error", "category"),
    [
        ("access_denied", "authorization_denied"),
        ("expired_token", "authorization_expired"),
    ],
)
async def test_json_login_reports_stable_device_failures(
    token_error: str,
    category: str,
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_horizon_api(HorizonAuthAPI(token_error=token_error))

    with pytest.raises(SystemExit, match="1"):
        await login(json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result["error"]["category"] == category
    assert CredentialStore().path.exists() is False


async def test_logout_revokes_the_remote_key_and_clears_local_state(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = HorizonAuthAPI()
    use_horizon_api(api)
    CredentialStore().save("fmcp_stored_key")

    await logout(json_output=True)

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "command": "logout",
        "localCredentialRemoved": True,
        "remoteRevoked": True,
    }
    assert CredentialStore().path.exists() is False
    assert any(
        request.method == "DELETE" and request.url.path == "/api/v0/me/api-key"
        for request in api.requests
    )


async def test_logout_clears_local_state_when_remote_revocation_fails(
    use_horizon_api: Callable[[HorizonAuthAPI], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_horizon_api(HorizonAuthAPI(revoke_status=503))
    CredentialStore().save("fmcp_stored_key")

    with pytest.raises(SystemExit, match="1"):
        await logout(json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result["error"]["category"] == "remote_revocation_failed"
    assert result["localCredentialRemoved"] is True
    assert result["remoteCredentialMayRemain"] is True
    assert CredentialStore().path.exists() is False
