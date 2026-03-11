"""JWT-backed product-layer authentication for PureCipher registry."""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import jwt


class RegistryRole(Enum):
    """Supported PureCipher UI roles."""

    VIEWER = "viewer"
    PUBLISHER = "publisher"
    REVIEWER = "reviewer"
    ADMIN = "admin"


@dataclass(frozen=True)
class RegistryUser:
    """Static user record used for login."""

    username: str
    password: str
    role: RegistryRole
    display_name: str


@dataclass(frozen=True)
class RegistrySession:
    """Authenticated UI/API session."""

    username: str
    role: RegistryRole
    display_name: str
    expires_at: str

    def has_any_role(self, *roles: RegistryRole) -> bool:
        """Return True when the session matches one of the allowed roles."""

        return self.role in set(roles)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for UI and JSON responses."""

        return {
            "username": self.username,
            "role": self.role.value,
            "display_name": self.display_name,
            "expires_at": self.expires_at,
            "can_submit": self.role
            in {RegistryRole.PUBLISHER, RegistryRole.REVIEWER, RegistryRole.ADMIN},
            "can_review": self.role in {RegistryRole.REVIEWER, RegistryRole.ADMIN},
            "can_admin": self.role == RegistryRole.ADMIN,
        }


@dataclass(frozen=True)
class RegistryAuthSettings:
    """Configuration for product-layer JWT auth."""

    enabled: bool = False
    jwt_secret: str = ""
    issuer: str = "purecipher-registry"
    audience: str = "purecipher-registry"
    cookie_name: str = "purecipher_registry_token"
    token_ttl_seconds: int = 12 * 60 * 60
    users: tuple[RegistryUser, ...] = ()

    @classmethod
    def from_env(
        cls,
        *,
        issuer: str,
        signing_secret: bytes | str | None = None,
    ) -> RegistryAuthSettings:
        """Create auth settings from env vars."""

        enabled = _env_flag("PURECIPHER_ENABLE_AUTH")
        raw_secret = os.getenv("PURECIPHER_JWT_SECRET", "")
        raw_secret = _resolve_secret(
            raw_secret=raw_secret,
            signing_secret=signing_secret,
        )
        users = tuple(
            _parse_users(
                raw_users=os.getenv("PURECIPHER_USERS_JSON", ""),
                enabled=enabled,
            )
        )
        return cls(
            enabled=enabled,
            jwt_secret=raw_secret,
            issuer=os.getenv("PURECIPHER_JWT_ISSUER", issuer),
            audience=os.getenv("PURECIPHER_JWT_AUDIENCE", "purecipher-registry"),
            cookie_name=os.getenv(
                "PURECIPHER_AUTH_COOKIE_NAME",
                "purecipher_registry_token",
            ),
            token_ttl_seconds=_env_int(
                "PURECIPHER_JWT_TTL_SECONDS",
                12 * 60 * 60,
            ),
            users=users,
        )

    @classmethod
    def from_values(
        cls,
        *,
        enabled: bool,
        issuer: str,
        signing_secret: bytes | str | None = None,
        jwt_secret: str = "",
        audience: str = "purecipher-registry",
        cookie_name: str = "purecipher_registry_token",
        token_ttl_seconds: int = 12 * 60 * 60,
        users_json: str = "",
    ) -> RegistryAuthSettings:
        """Create auth settings from explicit runtime inputs."""

        users = tuple(_parse_users(raw_users=users_json, enabled=enabled))
        return cls(
            enabled=enabled,
            jwt_secret=_resolve_secret(
                raw_secret=jwt_secret,
                signing_secret=signing_secret,
            ),
            issuer=issuer,
            audience=audience,
            cookie_name=cookie_name,
            token_ttl_seconds=token_ttl_seconds,
            users=users,
        )

    def validate(self) -> None:
        """Validate settings when auth is enabled."""

        if not self.enabled:
            return
        if not self.jwt_secret:
            raise ValueError(
                "PureCipher auth is enabled but no JWT secret is configured."
            )
        if not self.users:
            raise ValueError("PureCipher auth is enabled but no users are configured.")

    def authenticate(self, username: str, password: str) -> RegistryUser | None:
        """Authenticate username/password against configured users."""

        for user in self.users:
            if hmac.compare_digest(user.username, username) and hmac.compare_digest(
                user.password, password
            ):
                return user
        return None

    def issue_token(self, user: RegistryUser) -> str:
        """Issue a signed JWT for a user."""

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.token_ttl_seconds)
        payload = {
            "sub": user.username,
            "role": user.role.value,
            "name": user.display_name,
            "iss": self.issuer,
            "aud": self.audience,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")

    def decode_token(self, token: str) -> RegistrySession | None:
        """Decode and validate a JWT."""

        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                issuer=self.issuer,
                audience=self.audience,
            )
        except jwt.PyJWTError:
            return None

        role_value = payload.get("role")
        username = str(payload.get("sub") or "")
        if not username or not role_value:
            return None

        try:
            role = RegistryRole(str(role_value))
        except ValueError:
            return None

        exp = payload.get("exp")
        expires_at = (
            datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
            if isinstance(exp, int | float)
            else ""
        )
        return RegistrySession(
            username=username,
            role=role,
            display_name=str(payload.get("name") or username),
            expires_at=expires_at,
        )


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _resolve_secret(*, raw_secret: str, signing_secret: bytes | str | None) -> str:
    if raw_secret:
        return raw_secret
    if isinstance(signing_secret, str):
        return signing_secret
    if isinstance(signing_secret, bytes):
        return signing_secret.decode("utf-8", errors="ignore")
    return ""


def _parse_users(*, raw_users: str, enabled: bool) -> list[RegistryUser]:
    if raw_users.strip():
        parsed = json.loads(raw_users)
        if not isinstance(parsed, list):
            raise ValueError("PURECIPHER_USERS_JSON must be a JSON array.")
        return [_coerce_user(item) for item in parsed]
    if enabled:
        return list(_default_dev_users())
    return []


def _coerce_user(value: Any) -> RegistryUser:
    if not isinstance(value, dict):
        raise ValueError("Each PureCipher auth user must be an object.")
    username = str(value.get("username") or "").strip()
    password = str(value.get("password") or "").strip()
    role = RegistryRole(str(value.get("role") or RegistryRole.VIEWER.value))
    display_name = str(value.get("display_name") or username or role.value.title())
    if not username or not password:
        raise ValueError("PureCipher auth users require username and password.")
    return RegistryUser(
        username=username,
        password=password,
        role=role,
        display_name=display_name,
    )


def _default_dev_users() -> tuple[RegistryUser, ...]:
    return (
        RegistryUser(
            username="viewer",
            password="viewer123",
            role=RegistryRole.VIEWER,
            display_name="Registry Viewer",
        ),
        RegistryUser(
            username="admin",
            password="admin123",
            role=RegistryRole.ADMIN,
            display_name="Registry Admin",
        ),
        RegistryUser(
            username="reviewer",
            password="reviewer123",
            role=RegistryRole.REVIEWER,
            display_name="Registry Reviewer",
        ),
        RegistryUser(
            username="publisher",
            password="publisher123",
            role=RegistryRole.PUBLISHER,
            display_name="Registry Publisher",
        ),
    )


__all__ = [
    "RegistryAuthSettings",
    "RegistryRole",
    "RegistrySession",
    "RegistryUser",
]
