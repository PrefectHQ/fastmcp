import json

import pytest

from fastmcp.cli.deploy.horizon_client import DeviceAuthorization, HorizonUser
from fastmcp.cli.deploy.output import (
    emit_device_challenge,
    emit_environment_logout,
    emit_error,
    emit_identity,
    emit_logout,
)


def authorization() -> DeviceAuthorization:
    return DeviceAuthorization(
        device_code="device-secret",
        user_code="ABCD-EFGH",
        verification_uri="https://horizon.prefect.io/oauth/device",
        verification_uri_complete=(
            "https://horizon.prefect.io/oauth/device?user_code=ABCD-EFGH"
        ),
        expires_in=600,
        interval=5,
    )


def user() -> HorizonUser:
    return HorizonUser(id="user-1", email="ada@example.com", name="Ada")


def test_json_device_challenge_uses_only_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_device_challenge(authorization(), json_output=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "event": "device_authorization",
        "verificationUrl": "https://horizon.prefect.io/oauth/device",
        "verificationUrlComplete": (
            "https://horizon.prefect.io/oauth/device?user_code=ABCD-EFGH"
        ),
        "userCode": "ABCD-EFGH",
    }


def test_tty_device_challenge_uses_the_sign_in_layout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_device_challenge(authorization(), json_output=False)

    output = capsys.readouterr().out
    assert "│" in output
    assert "Deploy FastMCP on Horizon" in output
    assert "✓ Device authorization started" in output
    assert "https://horizon.prefect.io/oauth/device?user_code=ABCD-EFGH" in output
    assert "ABCD-EFGH" in output
    assert "The request expires in 10 minutes." in output


def test_json_identity_has_stable_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_identity("login", user(), json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": True,
        "command": "login",
        "user": {
            "id": "user-1",
            "email": "ada@example.com",
            "name": "Ada",
        },
    }


def test_tty_identity_uses_an_account_panel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_identity("whoami", user(), json_output=False)

    output = capsys.readouterr().out
    assert "│" in output
    assert "Horizon Account" in output
    assert "Ada" in output
    assert "ada@example.com" in output
    assert "● Signed in" in output
    assert "Organization" not in output


def test_json_error_has_stable_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_error(
        "logout",
        "remote_revocation_failed",
        "The remote key can remain active.",
        json_output=True,
        details={
            "localCredentialRemoved": True,
            "remoteCredentialMayRemain": True,
        },
    )

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": False,
        "command": "logout",
        "error": {
            "category": "remote_revocation_failed",
            "message": "The remote key can remain active.",
        },
        "localCredentialRemoved": True,
        "remoteCredentialMayRemain": True,
    }


def test_tty_environment_logout_explains_that_no_action_was_taken(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_environment_logout(json_output=False)

    output = capsys.readouterr().out
    assert "Horizon Account" in output
    assert "This session uses HORIZON_API_KEY." in output
    assert "Remove it from your environment to sign out." in output
    assert "No credential was revoked or removed." in output


def test_json_environment_logout_has_stable_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_environment_logout(json_output=True)

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "command": "logout",
        "credentialSource": "environment",
        "localCredentialRemoved": False,
        "remoteRevoked": False,
    }


def test_tty_logout_uses_the_horizon_header(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_logout(remote_revoked=True, json_output=False)

    output = capsys.readouterr().out
    assert "Logged out of Horizon" in output
    assert "│" in output


def test_json_logout_has_stable_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_logout(remote_revoked=True, json_output=True)

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "command": "logout",
        "localCredentialRemoved": True,
        "remoteRevoked": True,
    }
