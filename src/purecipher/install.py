"""Install recipe generation for the PureCipher verified registry."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from fastmcp.server.security.gateway.tool_marketplace import ToolListing


@dataclass(frozen=True)
class InstallRecipe:
    """Concrete install or verification snippet for a verified listing."""

    recipe_id: str
    title: str
    description: str
    format: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the recipe for JSON responses."""

        return {
            "recipe_id": self.recipe_id,
            "title": self.title,
            "description": self.description,
            "format": self.format,
            "content": self.content,
            "metadata": dict(self.metadata),
        }


def _join_url(base_url: str | None, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"


def _string_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(key): str(item)
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }
    if isinstance(value, list):
        return {str(item): f"${{{item}}}" for item in value if str(item).strip()}
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return []


def _docker_compose_content(
    *,
    service_name: str,
    docker_image: str,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> str:
    lines = [
        "services:",
        f"  {service_name}:",
        f"    image: {docker_image}",
        "    restart: unless-stopped",
    ]
    if command:
        rendered = ", ".join(json.dumps(value) for value in [command, *args])
        lines.append(f"    command: [{rendered}]")
    if env:
        lines.append("    environment:")
        for key, value in env.items():
            lines.append(f"      {key}: {value}")
    return "\n".join(lines)


def build_install_recipes(
    listing: ToolListing,
    *,
    registry_prefix: str = "/registry",
    registry_base_url: str | None = None,
) -> list[InstallRecipe]:
    """Build metadata-driven install snippets for a verified listing."""

    metadata = dict(listing.metadata)
    env = _string_dict(metadata.get("env") or metadata.get("env_vars"))
    args = _string_list(metadata.get("args") or metadata.get("command_args"))
    command = str(metadata.get("command") or "").strip()
    endpoint = str(
        metadata.get("endpoint") or metadata.get("mcp_endpoint") or ""
    ).strip()
    transport = str(metadata.get("transport") or "streamable-http").strip()
    docker_image = str(metadata.get("docker_image") or "").strip()
    quoted_tool_name = quote(listing.tool_name, safe="")

    detail_url = _join_url(
        registry_base_url,
        f"{registry_prefix}/listings/{quoted_tool_name}",
    )
    api_url = _join_url(
        registry_base_url,
        f"{registry_prefix}/tools/{quoted_tool_name}",
    )
    verify_url = _join_url(
        registry_base_url,
        f"{registry_prefix}/verify",
    )

    recipes = [
        InstallRecipe(
            recipe_id="registry_reference",
            title="Registry Reference",
            description=(
                "Canonical listing metadata, attestation issuer, and verification "
                "endpoints for this PureCipher entry."
            ),
            format="json",
            content=json.dumps(
                {
                    "tool_name": listing.tool_name,
                    "display_name": listing.display_name,
                    "version": listing.version,
                    "publisher": listing.author,
                    "certification_level": listing.certification_level.value,
                    "detail_url": detail_url,
                    "api_url": api_url,
                    "verify_url": verify_url,
                    "manifest_digest": (
                        listing.attestation.manifest_digest
                        if listing.attestation is not None
                        else ""
                    ),
                },
                indent=2,
            ),
            metadata={"kind": "registry"},
        )
    ]

    if endpoint:
        payload: dict[str, Any] = {
            "mcpServers": {
                listing.tool_name: {
                    "transport": transport,
                    "url": endpoint,
                }
            }
        }
        recipes.append(
            InstallRecipe(
                recipe_id="mcp_client_http",
                title="MCP Client Config (HTTP)",
                description=(
                    "Client configuration for connecting to the publisher's "
                    "streamable HTTP or SSE endpoint."
                ),
                format="json",
                content=json.dumps(payload, indent=2),
                metadata={"kind": "client_config", "transport": transport},
            )
        )

    if command:
        server_config: dict[str, Any] = {"command": command}
        if args:
            server_config["args"] = args
        if env:
            server_config["env"] = env
        recipes.append(
            InstallRecipe(
                recipe_id="mcp_client_stdio",
                title="MCP Client Config (stdio)",
                description=(
                    "Client configuration for launching the published tool as a "
                    "local stdio MCP server."
                ),
                format="json",
                content=json.dumps(
                    {"mcpServers": {listing.tool_name: server_config}},
                    indent=2,
                ),
                metadata={"kind": "client_config", "transport": "stdio"},
            )
        )

    if docker_image:
        recipes.append(
            InstallRecipe(
                recipe_id="docker_compose",
                title="Docker Compose",
                description=(
                    "Minimal container runtime for the published tool. Pair this "
                    "with the client config recipe that matches the tool's transport."
                ),
                format="yaml",
                content=_docker_compose_content(
                    service_name=listing.tool_name.replace("_", "-"),
                    docker_image=docker_image,
                    command=command,
                    args=args,
                    env=env,
                ),
                metadata={"kind": "docker", "image": docker_image},
            )
        )

    recipes.append(
        InstallRecipe(
            recipe_id="verify_attestation",
            title="Verify Attestation",
            description=(
                "Confirm the listing attestation against the stored manifest before "
                "you wire it into a client or runtime."
            ),
            format="bash",
            content=(
                "curl -s -X POST "
                f"{json.dumps(verify_url)} "
                "-H 'Content-Type: application/json' "
                f"-d '{json.dumps({'tool_name': listing.tool_name})}'"
            ),
            metadata={"kind": "verification"},
        )
    )

    return recipes


__all__ = ["InstallRecipe", "build_install_recipes"]
