from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
import traceback
from pathlib import Path
from typing import cast

import httpx2
import pytest
from pydantic import SecretStr

from fastmcp.cli.deploy.configuration import (
    ConfigurationStore,
    HorizonConfiguration,
)
from fastmcp.cli.deploy.credentials import (
    AuthenticationRequiredError,
    CredentialStore,
    resolve_credential,
    revoke_and_clear_credential,
)
from fastmcp.cli.deploy.horizon_client import HorizonClient, HorizonUnavailableError
from fastmcp.cli.deploy.state import (
    StateFileError,
    _restrict_windows_access,
)


def load_secret(store: CredentialStore) -> SecretStr:
    secret = store.load()
    assert secret is not None
    return secret


def test_credential_store_writes_only_the_approved_contract(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)
    store.save("fmcp_secret")

    assert json.loads(store.path.read_text()) == {
        "schemaVersion": 1,
        "apiKey": "fmcp_secret",
    }
    assert load_secret(store).get_secret_value() == "fmcp_secret"
    assert "user" not in store.path.read_text()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_credential_store_restricts_file_and_directory_modes(tmp_path: Path) -> None:
    state_directory = tmp_path / "cli"
    store = CredentialStore(state_directory)
    store.save("fmcp_secret")

    assert store.path.stat().st_mode & 0o777 == 0o600
    assert state_directory.stat().st_mode & 0o777 == 0o700


def test_credential_store_restricts_an_existing_secret_file(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)
    store.path.write_text('{"schemaVersion": 1, "apiKey": "fmcp_secret"}')
    if os.name != "nt":
        store.path.chmod(0o644)

    assert load_secret(store).get_secret_value() == "fmcp_secret"
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync")
def test_atomic_write_ignores_unsupported_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CredentialStore(tmp_path)
    original_fsync = os.fsync

    def fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "directory sync is not supported")
        original_fsync(descriptor)

    monkeypatch.setattr("fastmcp.cli.deploy.state.os.fsync", fsync)

    store.save("fmcp_secret")

    assert load_secret(store).get_secret_value() == "fmcp_secret"


def test_atomic_write_preserves_previous_state_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CredentialStore(tmp_path)
    store.save("fmcp_original")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("fastmcp.cli.deploy.state.os.replace", fail_replace)
    with pytest.raises(StateFileError):
        store.save("fmcp_new")

    assert json.loads(store.path.read_text())["apiKey"] == "fmcp_original"
    assert list(tmp_path.glob(".*.tmp")) == []


async def test_environment_credential_takes_precedence_and_is_not_stored(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path)
    store.save("fmcp_stored")
    authorize_called = False

    async def authorize() -> SecretStr:
        nonlocal authorize_called
        authorize_called = True
        return SecretStr("fmcp_interactive")

    result = await resolve_credential(
        store,
        environ={"HORIZON_API_KEY": "fmcp_environment"},
        authorize=authorize,
    )

    assert result.source == "environment"
    assert result.api_key.get_secret_value() == "fmcp_environment"
    assert load_secret(store).get_secret_value() == "fmcp_stored"
    assert authorize_called is False


async def test_stored_credential_precedes_interactive_authorization(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path)
    store.save("fmcp_stored")

    async def authorize() -> SecretStr:
        raise AssertionError("interactive authorization must not run")

    result = await resolve_credential(store, environ={}, authorize=authorize)

    assert result.source == "stored"
    assert result.api_key.get_secret_value() == "fmcp_stored"


async def test_interactive_credential_is_persisted(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)

    async def authorize() -> SecretStr:
        return SecretStr("fmcp_interactive")

    result = await resolve_credential(store, environ={}, authorize=authorize)

    assert result.source == "interactive"
    assert load_secret(store).get_secret_value() == "fmcp_interactive"


async def test_interactive_credential_rejects_an_origin_change(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path)
    ConfigurationStore(tmp_path).save(
        HorizonConfiguration(
            schemaVersion=1,
            apiOrigin="https://dev.horizon.prefect.io",
        )
    )

    async def authorize() -> SecretStr:
        return SecretStr("fmcp_old_origin")

    with pytest.raises(StateFileError, match="host changed"):
        await resolve_credential(
            store,
            environ={},
            authorize=authorize,
            expected_api_origin="https://horizon.prefect.io",
        )

    assert store.load() is None


async def test_missing_noninteractive_credential_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(AuthenticationRequiredError):
        await resolve_credential(CredentialStore(tmp_path), environ={})


async def test_remote_revoke_always_removes_the_local_credential(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path)
    store.save("fmcp_stored")

    def unavailable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    async with HorizonClient(
        api_key="fmcp_stored",
        transport=httpx2.MockTransport(unavailable),
    ) as client:
        with pytest.raises(HorizonUnavailableError):
            await revoke_and_clear_credential(client, store)

    assert store.load() is None


def test_windows_acl_replaces_the_existing_access_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{}")
    calls: list[list[str]] = []
    state_paths: list[str] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        environment = cast(dict[str, str], kwargs["env"])
        state_paths.append(environment["FASTMCP_STATE_PATH"])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("fastmcp.cli.deploy.state.subprocess.run", run)
    _restrict_windows_access(path)

    assert calls == [
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            calls[0][5],
        ]
    ]
    assert state_paths == [str(path)]
    assert "$path = $env:FASTMCP_STATE_PATH" in calls[0][5]
    assert "Get-Acl -LiteralPath $path" in calls[0][5]
    assert "SetAccessRuleProtection($true, $false)" in calls[0][5]
    assert "RemoveAccessRuleSpecific($existingRule)" in calls[0][5]


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL inspection")
def test_windows_credential_state_allows_only_the_current_user(tmp_path: Path) -> None:
    state_directory = tmp_path / "cli"
    store = CredentialStore(state_directory)
    store.save("fmcp_secret")
    inspect_acl = r"""
$acl = Get-Acl -LiteralPath $env:FASTMCP_STATE_PATH
$current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$access = @($acl.Access | ForEach-Object {
    $_.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]
    ).Value
})
[pscustomobject]@{
    current = $current
    access = $access
    protected = $acl.AreAccessRulesProtected
    inherited = @($acl.Access | ForEach-Object { $_.IsInherited })
} | ConvertTo-Json -Compress
"""

    for path in (state_directory, store.path):
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                inspect_acl,
            ],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "FASTMCP_STATE_PATH": str(path)},
        )
        acl = json.loads(result.stdout)
        assert set(acl["access"]) == {acl["current"]}
        assert acl["protected"] is True
        assert not any(acl["inherited"])


def test_credential_store_rejects_empty_api_keys(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)

    for api_key in ("", "   "):
        with pytest.raises(StateFileError):
            store.save(api_key)

    assert store.path.exists() is False


def test_malformed_credential_state_has_a_safe_error(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path)
    store.path.write_text(
        '{"schemaVersion": 1, "apiKey": "fmcp_valid", "metadata": "fmcp_secret"}'
    )

    with pytest.raises(StateFileError) as exc_info:
        store.load()

    formatted_exception = "".join(traceback.format_exception(exc_info.value))
    assert "fmcp_secret" not in formatted_exception
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
