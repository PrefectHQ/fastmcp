"""Public Prefect Horizon authentication commands."""

from __future__ import annotations

import os
import platform
import sys
import webbrowser
from typing import Annotated, NoReturn

from cyclopts import Parameter
from pydantic import SecretStr
from rich.status import Status

import fastmcp
from fastmcp.cli.deploy.authentication import (
    DeviceAuthorizationDeniedError,
    DeviceAuthorizationError,
    DeviceAuthorizationExpiredError,
    authorize_device,
)
from fastmcp.cli.deploy.configuration import (
    ConfigurationStore,
    HorizonConfiguration,
)
from fastmcp.cli.deploy.credentials import (
    AuthenticationRequiredError,
    CredentialStore,
    ResolvedCredential,
)
from fastmcp.cli.deploy.horizon_client import (
    DeviceAuthorization,
    DeviceMetadata,
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
    emit_environment_logout,
    emit_error,
    emit_identity,
    emit_logout,
    start_device_approval_status,
    stop_device_approval_status,
)
from fastmcp.cli.deploy.state import StateFileError, state_lock

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


def _device_metadata() -> DeviceMetadata:
    return DeviceMetadata(
        device_name=platform.node() or None,
        platform=platform.system().lower() or None,
        architecture=platform.machine().lower() or None,
        client_version=fastmcp.__version__,
    )


def _load_session_snapshot(
    credentials: CredentialStore,
) -> tuple[HorizonConfiguration, ResolvedCredential | None]:
    with state_lock(credentials.path.parent):
        configuration = ConfigurationStore(credentials.path.parent).load()
        environment_key = os.environ.get("HORIZON_API_KEY")
        if environment_key:
            credential = ResolvedCredential(
                api_key=SecretStr(environment_key),
                source="environment",
            )
        else:
            stored_key = credentials.load()
            credential = (
                ResolvedCredential(api_key=stored_key, source="stored")
                if stored_key is not None
                else None
            )
    return configuration, credential


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
        requested_configuration: HorizonConfiguration | None = None
        if host is not None:
            try:
                requested_configuration = configuration_store.set_api_origin(
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

        configuration, credential = _load_session_snapshot(credentials)
        if (
            requested_configuration is not None
            and configuration.api_origin != requested_configuration.api_origin
        ):
            raise StateFileError("The Horizon host changed during login")

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
                        metadata=_device_metadata(),
                        on_challenge=show_challenge,
                        open_browser=not json_output and _can_open_browser(),
                        browser_opener=webbrowser.open,
                    )
            finally:
                stop_device_approval_status(approval_status)

        async def interactive_credential() -> ResolvedCredential:
            api_key = await device_authorization()
            credentials.save_for_origin(
                api_key,
                expected_api_origin=configuration.api_origin,
            )
            return ResolvedCredential(api_key=api_key, source="interactive")

        if credential is None:
            credential = await interactive_credential()

        try:
            user = await _get_user(
                configuration.api_origin,
                credential,
            )
        except HorizonUnauthorizedError:
            if credential.source == "environment":
                raise

            credentials.clear_if_matches(
                credential.api_key,
                expected_api_origin=configuration.api_origin,
            )
            if credential.source == "interactive":
                raise

            credential = await interactive_credential()
            try:
                user = await _get_user(
                    configuration.api_origin,
                    credential,
                )
            except HorizonUnauthorizedError:
                credentials.clear_if_matches(
                    credential.api_key,
                    expected_api_origin=configuration.api_origin,
                )
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
    configuration: HorizonConfiguration | None = None
    credential: ResolvedCredential | None = None

    try:
        configuration, credential = _load_session_snapshot(credentials)
        if credential is None:
            raise AuthenticationRequiredError("Horizon authentication is required")
        user = await _get_user(
            configuration.api_origin,
            credential,
        )
    except HorizonUnauthorizedError as error:
        if (
            configuration is not None
            and credential is not None
            and credential.source == "stored"
        ):
            try:
                credentials.clear_if_matches(
                    credential.api_key,
                    expected_api_origin=configuration.api_origin,
                )
            except StateFileError as cleanup_error:
                _fail_for_expected_error(
                    "whoami",
                    cleanup_error,
                    json_output=json_output,
                )
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

    if os.environ.get("HORIZON_API_KEY"):
        emit_environment_logout(json_output=json_output)
        return

    try:
        configuration, credential = _load_session_snapshot(credentials)
        if credential is None:
            emit_logout(remote_revoked=False, json_output=json_output)
            return

        async with HorizonClient(
            configuration.api_origin,
            api_key=credential.api_key,
        ) as client:
            try:
                await client.revoke_current_api_key()
            finally:
                credentials.clear_if_matches(
                    credential.api_key,
                    expected_api_origin=configuration.api_origin,
                )
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
