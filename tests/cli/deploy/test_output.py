import json

import pytest

from fastmcp.cli.deploy.horizon_client import (
    DeviceAuthorization,
    HorizonOrganization,
    HorizonUser,
)
from fastmcp.cli.deploy.output import (
    emit_device_challenge,
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


def organizations() -> tuple[HorizonOrganization, ...]:
    return (
        HorizonOrganization(id="org-1", name="Acme", slug="acme"),
        HorizonOrganization(id="org-2", name="Research", slug="research"),
    )


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


def test_json_identity_has_stable_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_identity("login", user(), organizations(), json_output=True)

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": True,
        "command": "login",
        "user": {
            "id": "user-1",
            "email": "ada@example.com",
            "name": "Ada",
        },
        "organizations": [
            {"id": "org-1", "name": "Acme", "slug": "acme"},
            {"id": "org-2", "name": "Research", "slug": "research"},
        ],
    }


def test_tty_identity_handles_no_organizations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_identity("whoami", user(), (), json_output=False)

    output = capsys.readouterr().out
    assert "Authenticated as Ada <ada@example.com>." in output
    assert "Organization memberships: none" in output


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
