"""CLI launcher for PureCipher registry."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from fastmcp.server.security.certification.attestation import CertificationLevel
from purecipher.auth import RegistryAuthSettings
from purecipher.registry import PureCipherRegistry


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    """Create the PureCipher registry CLI parser."""

    parser = argparse.ArgumentParser(
        prog="purecipher-registry",
        description="Launch the PureCipher Secured MCP Registry.",
    )
    parser.add_argument("--name", default="purecipher-registry")
    parser.add_argument(
        "--signing-secret",
        default=os.getenv("PURECIPHER_SIGNING_SECRET"),
        help="Registry signing secret. Defaults to PURECIPHER_SIGNING_SECRET.",
    )
    parser.add_argument("--issuer-id", default="purecipher-registry")
    parser.add_argument(
        "--minimum-certification",
        default=CertificationLevel.BASIC.value,
        choices=[level.value for level in CertificationLevel],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--registry-prefix", default="/registry")
    parser.add_argument(
        "--database-path",
        default=os.getenv("PURECIPHER_REGISTRY_DB"),
        help="SQLite database path for persistence. Defaults to PURECIPHER_REGISTRY_DB.",
    )
    parser.add_argument(
        "--disable-security-api",
        action="store_true",
        help="Disable the broader /security API surface.",
    )
    parser.add_argument(
        "--require-moderation",
        action="store_true",
        default=_env_flag("PURECIPHER_REQUIRE_MODERATION"),
        help="Place accepted submissions into pending review instead of immediate publication.",
    )
    parser.add_argument(
        "--enable-auth",
        action="store_true",
        default=_env_flag("PURECIPHER_ENABLE_AUTH"),
        help="Enable JWT login and role-based access for registry routes.",
    )
    parser.add_argument(
        "--jwt-secret",
        default=os.getenv("PURECIPHER_JWT_SECRET", ""),
        help="JWT secret for registry auth. Defaults to PURECIPHER_JWT_SECRET.",
    )
    parser.add_argument(
        "--jwt-audience",
        default=os.getenv("PURECIPHER_JWT_AUDIENCE", "purecipher-registry"),
        help="JWT audience used for issued registry tokens.",
    )
    parser.add_argument(
        "--auth-cookie-name",
        default=os.getenv("PURECIPHER_AUTH_COOKIE_NAME", "purecipher_registry_token"),
        help="Cookie name used for browser registry sessions.",
    )
    parser.add_argument(
        "--jwt-ttl-seconds",
        type=int,
        default=int(os.getenv("PURECIPHER_JWT_TTL_SECONDS", "43200")),
        help="JWT lifetime in seconds.",
    )
    parser.add_argument(
        "--users-json",
        default=os.getenv("PURECIPHER_USERS_JSON", ""),
        help="JSON array of static auth users. Defaults to PURECIPHER_USERS_JSON.",
    )
    return parser


def build_registry_from_args(args: argparse.Namespace) -> PureCipherRegistry:
    """Instantiate a registry from parsed CLI arguments."""

    if not args.signing_secret:
        raise ValueError(
            "A signing secret is required. Pass --signing-secret or set PURECIPHER_SIGNING_SECRET."
        )

    auth_settings = RegistryAuthSettings.from_values(
        enabled=args.enable_auth,
        issuer=args.issuer_id,
        signing_secret=args.signing_secret,
        jwt_secret=args.jwt_secret,
        audience=args.jwt_audience,
        cookie_name=args.auth_cookie_name,
        token_ttl_seconds=args.jwt_ttl_seconds,
        users_json=args.users_json,
    )

    return PureCipherRegistry(
        args.name,
        signing_secret=args.signing_secret,
        issuer_id=args.issuer_id,
        minimum_certification=CertificationLevel(args.minimum_certification),
        registry_prefix=args.registry_prefix,
        persistence_path=args.database_path,
        mount_security_api=not args.disable_security_api,
        require_moderation=args.require_moderation,
        auth_settings=auth_settings,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the PureCipher registry server."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    registry = build_registry_from_args(args)
    registry.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
    )
    return 0


__all__ = [
    "build_parser",
    "build_registry_from_args",
    "main",
]
