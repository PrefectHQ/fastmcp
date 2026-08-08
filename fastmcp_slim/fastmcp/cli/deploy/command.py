"""Public Prefect Horizon authentication commands."""

from __future__ import annotations

import sys
import webbrowser
from typing import Annotated, NoReturn

from cyclopts import Parameter
from rich.status import Status

from fastmcp.cli.deploy.authentication import (
    DeviceAuthorizationDeniedError,
    DeviceAuthorizationError,
    DeviceAuthorizationExpiredError,
    authorize_device,
)
from fastmcp.cli.deploy.configuration import ConfigurationStore
from fastmcp.cli.deploy.credentials import (
    AuthenticationRequiredError,
    CredentialStore,
    ResolvedCredential,
    resolve_credential,
    revoke_and_clear_credential,
)
from fastmcp.cli.deploy.horizon_client import (
    DeviceAuthorization,
    HorizonClient,
    HorizonResponseError,
    HorizonUnauthorizedError,
    HorizonUnavailableError,
    HorizonUser,
)
from fastmcp.cli.deploy.output import (
    CommandName,
    ErrorCategory,
    emit_device_challenge,
    emit_error,
    emit_identity,
    emit_logout,
    start_device_approval_status,
    stop_device_approval_status,
)
from fastmcp.cli.deploy.state import StateFileError

JsonOption = Annotated[
    bool,
    Parameter(
        name="--json",
        help="Write one final JSON result to stdout",
        negative=(),
    ),
]
HostOption = Annotated[
    str | None,
    Parameter(
        name="--host",
        help="Use and save a different Horizon host URL",
    ),
]


def _can_open_browser() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _fail(
    command: CommandName,
    category: ErrorCategory,
    message: str,
    *,
    json_output: bool,
    details: dict[str, object] | None = None,
) -> NoReturn:
    emit_error(
        command,
        category,
        message,
        json_output=json_output,
        details=details,
    )
    raise SystemExit(1)


def _fail_for_expected_error(
    command: CommandName,
    error: Exception,
    *,
    json_output: bool,
) -> NoReturn:
    if isinstance(error, AuthenticationRequiredError):
        _fail(
            command,
            "authentication_required",
            "Run `fastmcp login` to sign in to Prefect Horizon.",
            json_output=json_output,
        )
    if isinstance(error, HorizonUnauthorizedError):
        _fail(
            command,
            "authentication_invalid",
            "The Horizon credential is not valid. Run `fastmcp login` again.",
            json_output=json_output,
        )
    if isinstance(error, DeviceAuthorizationDeniedError):
        _fail(
            command,
            "authorization_denied",
            "The device authorization request was denied.",
            json_output=json_output,
        )
    if isinstance(error, DeviceAuthorizationExpiredError):
        _fail(
            command,
            "authorization_expired",
            "The device authorization request expired. Run the command again.",
            json_output=json_output,
        )
    if isinstance(error, DeviceAuthorizationError):
        _fail(
            command,
            "authorization_failed",
            "The device authorization request failed. Run the command again.",
            json_output=json_output,
        )
    if isinstance(error, HorizonUnavailableError):
        _fail(
            command,
            "horizon_unavailable",
            "The Horizon API is unavailable. Try again later.",
            json_output=json_output,
        )
    if isinstance(error, HorizonResponseError):
        _fail(
            command,
            "horizon_error",
            "Horizon returned an unexpected response. Try again later.",
            json_output=json_output,
        )
    if isinstance(error, StateFileError):
        _fail(
            command,
            "state_error",
            "The local Horizon state is invalid.",
            json_output=json_output,
        )
    raise error


async def _get_user(
    api_origin: str,
    credential: ResolvedCredential,
) -> HorizonUser:
    async with HorizonClient(api_origin, api_key=credential.api_key) as client:
        return await client.get_current_user()


async def login(
    *,
    host: HostOption = None,
    json_output: JsonOption = False,
) -> None:
    """Sign in to Prefect Horizon."""
    credentials = CredentialStore()

    try:
        configuration_store = ConfigurationStore()
        if host is None:
            configuration = configuration_store.load()
        else:
            try:
                configuration = configuration_store.set_api_origin(
                    host,
                    credentials=credentials,
                )
            except ValueError:
                _fail(
                    "login",
                    "invalid_host",
                    "The Horizon host must be an HTTP origin.",
                    json_output=json_output,
                )

        async def device_authorization():
            approval_status: Status | None = None

            def show_challenge(challenge: DeviceAuthorization) -> None:
                nonlocal approval_status
                emit_device_challenge(challenge, json_output=json_output)
                approval_status = start_device_approval_status(json_output=json_output)

            try:
                async with HorizonClient(configuration.api_origin) as client:
                    return await authorize_device(
                        client,
                        on_challenge=show_challenge,
                        open_browser=not json_output and _can_open_browser(),
                        browser_opener=webbrowser.open,
                    )
            finally:
                stop_device_approval_status(approval_status)

        credential = await resolve_credential(
            credentials,
            authorize=device_authorization,
        )

        try:
            user = await _get_user(
                configuration.api_origin,
                credential,
            )
        except HorizonUnauthorizedError:
            if credential.source == "interactive":
                credentials.clear()
                raise
            if credential.source == "environment":
                raise

            credentials.clear()
            credential = await resolve_credential(
                credentials,
                authorize=device_authorization,
            )
            try:
                user = await _get_user(
                    configuration.api_origin,
                    credential,
                )
            except HorizonUnauthorizedError:
                credentials.clear()
                raise
    except (
        AuthenticationRequiredError,
        DeviceAuthorizationError,
        HorizonResponseError,
        HorizonUnauthorizedError,
        HorizonUnavailableError,
        StateFileError,
    ) as error:
        _fail_for_expected_error("login", error, json_output=json_output)

    emit_identity(
        "login",
        user,
        json_output=json_output,
    )


async def whoami(
    *,
    json_output: JsonOption = False,
) -> None:
    """Show the current Prefect Horizon user."""
    credentials = CredentialStore()
    credential: ResolvedCredential | None = None

    try:
        configuration = ConfigurationStore().load()
        credential = await resolve_credential(credentials)
        user = await _get_user(
            configuration.api_origin,
            credential,
        )
    except HorizonUnauthorizedError as error:
        if credential is not None and credential.source == "stored":
            credentials.clear()
        _fail_for_expected_error("whoami", error, json_output=json_output)
    except (
        AuthenticationRequiredError,
        HorizonResponseError,
        HorizonUnavailableError,
        StateFileError,
    ) as error:
        _fail_for_expected_error("whoami", error, json_output=json_output)

    emit_identity(
        "whoami",
        user,
        json_output=json_output,
    )


async def logout(
    *,
    json_output: JsonOption = False,
) -> None:
    """Revoke the current Horizon key and remove the local credential."""
    credentials = CredentialStore()

    try:
        configuration = ConfigurationStore().load()
        credential = await resolve_credential(credentials)
    except AuthenticationRequiredError:
        emit_logout(remote_revoked=False, json_output=json_output)
        return
    except StateFileError:
        try:
            credentials.clear()
        except StateFileError as error:
            _fail_for_expected_error("logout", error, json_output=json_output)
        _fail(
            "logout",
            "remote_revocation_failed",
            "The local credential was removed, but the remote key can remain active.",
            json_output=json_output,
            details={
                "localCredentialRemoved": True,
                "remoteCredentialMayRemain": True,
            },
        )

    try:
        async with HorizonClient(
            configuration.api_origin,
            api_key=credential.api_key,
        ) as client:
            await revoke_and_clear_credential(client, credentials)
    except HorizonUnauthorizedError:
        emit_logout(remote_revoked=False, json_output=json_output)
        return
    except (HorizonResponseError, HorizonUnavailableError):
        _fail(
            "logout",
            "remote_revocation_failed",
            "The local credential was removed, but the remote key can remain active.",
            json_output=json_output,
            details={
                "localCredentialRemoved": True,
                "remoteCredentialMayRemain": True,
            },
        )
    except StateFileError as error:
        _fail_for_expected_error("logout", error, json_output=json_output)

    emit_logout(remote_revoked=True, json_output=json_output)
