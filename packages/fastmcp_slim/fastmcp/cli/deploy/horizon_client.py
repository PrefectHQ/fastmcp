"""Typed HTTP client for the Horizon control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx2
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

DEVICE_AUTH_CLIENT_ID = "fastmcp-cli"
DEVICE_AUTH_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_HORIZON_API_ORIGIN = "https://horizon.prefect.io"

DeviceTokenError = Literal[
    "authorization_pending",
    "slow_down",
    "access_denied",
    "expired_token",
]


class HorizonError(RuntimeError):
    """A safe Horizon client error."""


class HorizonUnavailableError(HorizonError):
    """The Horizon API could not be reached."""


class HorizonUnauthorizedError(HorizonError):
    """The Horizon credential was rejected."""


class HorizonResponseError(HorizonError):
    """Horizon returned an unexpected response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


ResponseModelT = TypeVar("ResponseModelT", bound=_ResponseModel)


class DeviceAuthorization(_ResponseModel):
    device_code: Annotated[str, Field(min_length=1)]
    user_code: Annotated[str, Field(min_length=1)]
    verification_uri: Annotated[str, Field(pattern=r"^https?://")]
    verification_uri_complete: Annotated[str, Field(pattern=r"^https?://")]
    expires_in: Annotated[int, Field(gt=0)]
    interval: Annotated[int, Field(gt=0)]


class DeviceAccessToken(_ResponseModel):
    access_token: SecretStr
    token_type: Literal["Bearer"]

    @field_validator("access_token")
    @classmethod
    def require_nonempty_access_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("The access token is empty")
        return value


class _DeviceTokenErrorResponse(_ResponseModel):
    error: DeviceTokenError


class HorizonUser(_ResponseModel):
    id: str
    email: str
    name: str | None


class _CurrentUserResponse(_ResponseModel):
    user: HorizonUser


class HorizonOrganization(_ResponseModel):
    id: str
    name: str
    slug: str


class _PaginationMeta(_ResponseModel):
    nextCursor: str | None
    limit: int


class _OrganizationsResponse(_ResponseModel):
    items: tuple[HorizonOrganization, ...]
    meta: _PaginationMeta


@dataclass(frozen=True)
class DeviceMetadata:
    device_name: str | None = None
    platform: str | None = None
    architecture: str | None = None
    client_version: str | None = None


@dataclass(frozen=True)
class DeviceTokenPoll:
    access_token: SecretStr | None = None
    error: DeviceTokenError | None = None

    def __post_init__(self) -> None:
        if (self.access_token is None) == (self.error is None):
            raise ValueError("A device token poll must contain one result")


def normalize_api_origin(value: str) -> str:
    """Validate and normalize a Horizon API origin."""
    parts = urlsplit(value)
    try:
        _ = parts.port
    except ValueError:
        raise ValueError("The Horizon API origin must be an HTTP origin") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        raise ValueError("The Horizon API origin must be an HTTP origin")

    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class HorizonClient:
    """Call the Horizon routes used by FastMCP CLI authentication."""

    def __init__(
        self,
        api_origin: str = DEFAULT_HORIZON_API_ORIGIN,
        *,
        api_key: SecretStr | str | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_origin = normalize_api_origin(api_origin)
        self._api_key = (
            api_key
            if isinstance(api_key, SecretStr)
            else SecretStr(api_key)
            if api_key is not None
            else None
        )
        self._client = httpx2.AsyncClient(
            base_url=self.api_origin,
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> HorizonClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = False,
        data: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx2.Response:
        headers: dict[str, str] = {}
        if authenticated:
            if self._api_key is None:
                raise HorizonUnauthorizedError("Horizon authentication is required")
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"

        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                data=data,
                params=params,
            )
        except httpx2.RequestError as exc:
            raise HorizonUnavailableError("The Horizon API is unavailable") from exc

        if authenticated and response.status_code == 401:
            raise HorizonUnauthorizedError("The Horizon credential is not valid")
        return response

    @staticmethod
    def _validate_response(
        response: httpx2.Response,
        model: type[ResponseModelT],
    ) -> ResponseModelT:
        try:
            return model.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise HorizonResponseError(
                "Horizon returned an invalid response",
                status_code=response.status_code,
            ) from None

    @staticmethod
    def _require_status(response: httpx2.Response, expected: int) -> None:
        if response.status_code != expected:
            raise HorizonResponseError(
                "Horizon returned an unexpected status",
                status_code=response.status_code,
            )

    async def create_device_authorization(
        self,
        metadata: DeviceMetadata | None = None,
    ) -> DeviceAuthorization:
        metadata = metadata or DeviceMetadata()
        form = {
            "client_id": DEVICE_AUTH_CLIENT_ID,
            "device_name": metadata.device_name,
            "platform": metadata.platform,
            "architecture": metadata.architecture,
            "client_version": metadata.client_version,
        }
        response = await self._request(
            "POST",
            "/api/v0/oauth/device/authorization",
            data={key: value for key, value in form.items() if value is not None},
        )
        self._require_status(response, 200)
        return self._validate_response(response, DeviceAuthorization)

    async def exchange_device_authorization(
        self,
        device_code: str,
    ) -> DeviceTokenPoll:
        response = await self._request(
            "POST",
            "/api/v0/oauth/device/token",
            data={
                "grant_type": DEVICE_AUTH_GRANT_TYPE,
                "client_id": DEVICE_AUTH_CLIENT_ID,
                "device_code": device_code,
            },
        )

        if response.status_code == 200:
            result = self._validate_response(response, DeviceAccessToken)
            return DeviceTokenPoll(access_token=result.access_token)

        if response.status_code == 400:
            result = self._validate_response(response, _DeviceTokenErrorResponse)
            return DeviceTokenPoll(error=result.error)

        self._require_status(response, 200)
        raise AssertionError("unreachable")

    async def get_current_user(self) -> HorizonUser:
        response = await self._request(
            "GET",
            "/api/v0/me",
            authenticated=True,
        )
        self._require_status(response, 200)
        result = self._validate_response(response, _CurrentUserResponse)
        return result.user

    async def list_organizations(self) -> tuple[HorizonOrganization, ...]:
        organizations: list[HorizonOrganization] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            params = {"limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            response = await self._request(
                "GET",
                "/api/v0/me/organizations",
                authenticated=True,
                params=params,
            )
            self._require_status(response, 200)
            result = self._validate_response(response, _OrganizationsResponse)
            organizations.extend(result.items)

            cursor = result.meta.nextCursor
            if cursor is None:
                return tuple(organizations)
            if cursor in seen_cursors:
                raise HorizonResponseError(
                    "Horizon returned an invalid organization cursor",
                    status_code=response.status_code,
                )
            seen_cursors.add(cursor)

    async def revoke_current_api_key(self) -> None:
        response = await self._request(
            "DELETE",
            "/api/v0/me/api-key",
            authenticated=True,
        )
        self._require_status(response, 204)
