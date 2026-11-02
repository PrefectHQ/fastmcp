"""Skills-extension methods for the FastMCP client."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import mcp_types
from mcp.client.caching import CacheMode
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.telemetry import client_span
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillRequest,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsRequest,
    ListSkillsResult,
    MCPSkill,
    SkillResource,
    _parse_resource_uri,
)
from fastmcp.skills.skill import Skill, _validate_limits, _validate_relative_path
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client.client import Client

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250


def _trace_meta() -> mcp_types.RequestParamsMeta | None:
    return cast("mcp_types.RequestParamsMeta | None", inject_trace_context(None))


class ClientSkillsMixin:
    """Mixin providing SEP-2640 Skills methods for `Client`."""

    def _skills_unavailable_reason(self: Client) -> str | None:
        """Why this connection can't send Skills requests, or None if it can."""
        if self.protocol_version is None:
            return (
                "Client is not connected. Use the 'async with client:' context "
                "manager first."
            )
        if self.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            return "The Skills extension requires a modern MCP connection"

        client_extensions = self._session_kwargs.get("extensions") or {}
        if SKILLS_EXTENSION_ID not in client_extensions:
            return (
                "This client did not opt into the Skills extension; pass "
                "SkillsClientExtension() in Client(extensions=[...])"
            )

        capabilities = self.server_capabilities
        server_extensions = capabilities.extensions if capabilities else None
        if not server_extensions or SKILLS_EXTENSION_ID not in server_extensions:
            return "The server does not advertise the Skills extension"
        return None

    def _require_skills_extension(self: Client) -> None:
        reason = self._skills_unavailable_reason()
        if reason is not None:
            raise RuntimeError(reason)

    async def list_skills_mcp(
        self: Client,
        *,
        cursor: str | None = None,
        cache_mode: CacheMode = "use",
    ) -> ListSkillsResult:
        """Send one `skills/list` request and return its protocol result.

        Args:
            cursor: Optional pagination cursor from a previous result.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The complete protocol result, including cache and pagination fields.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_skills_extension()
        with client_span(
            "skills/list",
            "skills/list",
            "",
            session_id=self.transport.get_session_id(),
        ):

            async def _send() -> ListSkillsResult:
                request = ListSkillsRequest(
                    params=ListSkillsParams(cursor=cursor, meta=_trace_meta())
                )
                return await self._await_with_session_monitoring(
                    self.session.send_request(request, ListSkillsResult)
                )

            return await self._cached_fetch(
                "skills/list",
                cursor=cursor,
                cache_mode=cache_mode,
                send=_send,
            )

    async def list_skills(
        self: Client,
        max_pages: int = AUTO_PAGINATION_MAX_PAGES,
        *,
        cache_mode: CacheMode = "use",
    ) -> list[MCPSkill]:
        """Retrieve every page returned by `skills/list`.

        Args:
            max_pages: Maximum number of pages to fetch before raising.
            cache_mode: Response-cache behavior for the first page.

        Returns:
            Every skill entry returned across the server's pages.

        Raises:
            RuntimeError: If Skills was not negotiated or the page limit is reached.
            MCPError: If the server rejects a request.
        """
        skills: list[MCPSkill] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for _ in range(max_pages):
            result = await self.list_skills_mcp(cursor=cursor, cache_mode=cache_mode)
            skills.extend(result.skills)
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                logger.warning(
                    "[%s] Server returned duplicate pagination cursor %r for "
                    "list_skills; stopping pagination",
                    self.name,
                    result.next_cursor,
                )
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        else:
            raise RuntimeError(
                f"[{self.name}] Reached auto-pagination limit ({max_pages} pages) "
                "for list_skills. Use list_skills_mcp() for manual pagination, "
                "or increase max_pages."
            )

        return skills

    async def get_skill_mcp(
        self: Client,
        uri: str,
        *,
        cache_mode: CacheMode = "use",
    ) -> GetSkillResult:
        """Send a `skills/get` request and return its protocol result.

        Args:
            uri: URI of the skill's `SKILL.md` resource.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The complete protocol result, including cache fields.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        self._require_skills_extension()
        with client_span(
            "skills/get",
            "skills/get",
            uri,
            session_id=self.transport.get_session_id(),
            resource_uri=uri,
        ):

            async def _send() -> GetSkillResult:
                request = GetSkillRequest(
                    params=GetSkillParams(uri=uri, meta=_trace_meta())
                )
                return await self._await_with_session_monitoring(
                    self.session.send_request(request, GetSkillResult)
                )

            return await self._cached_fetch(
                "skills/get",
                cursor=None,
                params_key=uri,
                cache_mode=cache_mode,
                send=_send,
            )

    async def get_skill(
        self: Client,
        uri: str,
        *,
        cache_mode: CacheMode = "use",
    ) -> MCPSkill:
        """Retrieve one skill entry by its `SKILL.md` resource URI.

        Args:
            uri: URI of the skill's `SKILL.md` resource.
            cache_mode: Response-cache behavior for this call.

        Returns:
            The skill entry returned by the server.

        Raises:
            RuntimeError: If the client is disconnected or Skills was not negotiated.
            MCPError: If the server rejects the request.
        """
        result = await self.get_skill_mcp(uri, cache_mode=cache_mode)
        return result.skill

    async def read_skill(
        self: Client,
        uri: str,
        *,
        cache_mode: CacheMode = "use",
    ) -> Skill:
        """Fetch every file of a skill, verify each one, and return a `Skill`.

        The manifest comes from `skills/get` when the Skills extension was
        negotiated, and otherwise from the `_manifest` resource that FastMCP
        skill providers serve next to the files. Every file must match the
        size and SHA-256 digest the manifest promised. When an extension entry
        is available, the parsed frontmatter must also match that entry.

        Args:
            uri: URI of the skill's `SKILL.md` resource.
            cache_mode: Response-cache behavior for the `skills/get` request.

        Returns:
            The skill, with the exact bytes of every file.

        Raises:
            RuntimeError: If the client is disconnected.
            ValueError: If the manifest is unusable, a file does not match it,
                or the files do not form a valid skill.
            MCPError: If the server rejects a request.
        """
        _, authority, segments = _parse_resource_uri(uri)
        if not segments or segments[-1] != "SKILL.md":
            raise ValueError(f"read_skill takes a skill's SKILL.md URI, got {uri!r}")
        manifest, entry = await self._skill_manifest(uri, cache_mode)
        _validate_limits(
            len(manifest), sum(record.size for record in manifest.values())
        )
        for path in manifest:
            _validate_relative_path(path)
        files: dict[str, bytes] = {}
        for path, record in manifest.items():
            content = await self._read_skill_file(record.uri)
            if len(content) != record.size:
                raise ValueError(
                    f"Skill file {path!r} is {len(content)} bytes; "
                    f"the manifest promised {record.size}"
                )
            actual = f"sha256:{hashlib.sha256(content).hexdigest()}"
            if actual != record.digest:
                raise ValueError(
                    f"Skill file {path!r} does not match its manifest digest"
                )
            files[path] = content
        skill = Skill.from_files(files)
        if entry is not None and json.dumps(
            skill.frontmatter.root, sort_keys=True
        ) != json.dumps(entry.frontmatter.root, sort_keys=True):
            raise ValueError(f"Skill at {uri!r} does not match its entry's frontmatter")
        expected = segments[-2] if len(segments) > 1 else unquote(authority)
        if skill.name != expected:
            raise ValueError(
                f"Skill at {uri!r} is named {skill.name!r} in its SKILL.md"
            )
        return skill

    async def _skill_manifest(
        self: Client, uri: str, cache_mode: CacheMode
    ) -> tuple[dict[str, SkillResource], MCPSkill | None]:
        """File records and the entry whose frontmatter must match the content.

        A skill the server serves as resources but does not advertise through
        the extension, such as one behind a proxy, still has a `_manifest`.
        """
        if self._skills_unavailable_reason() is None:
            try:
                entry = await self.get_skill(uri, cache_mode=cache_mode)
            except MCPError as exc:
                if exc.error.code not in (INVALID_PARAMS, METHOD_NOT_FOUND):
                    raise
                entry = None
            if entry is not None and entry.resources == "dynamic":
                raise ValueError(f"Skill {uri!r} has no static manifest to read")
            if entry is not None and isinstance(entry.resources, list):
                root_depth = len(_parse_resource_uri(entry.uri)[2]) - 1
                manifest: dict[str, SkillResource] = {}
                for resource in entry.resources:
                    path = "/".join(_parse_resource_uri(resource.uri)[2][root_depth:])
                    if path in manifest:
                        raise ValueError(f"Duplicate skill file path: {path!r}")
                    manifest[path] = resource
                return manifest, entry
        return await self._resource_mode_manifest(uri), None

    async def _resource_mode_manifest(
        self: Client, uri: str
    ) -> dict[str, SkillResource]:
        parsed = urlsplit(uri)
        prefix = parsed.path[: -len("SKILL.md")]
        manifest_uri = urlunsplit(parsed._replace(path=f"{prefix}_manifest"))
        try:
            text = (await self._read_skill_file(manifest_uri)).decode("utf-8")
            data = json.loads(text)
            files = {
                entry["path"]: SkillResource(
                    uri=urlunsplit(
                        parsed._replace(path=prefix + quote(entry["path"], safe="/"))
                    ),
                    digest=entry["hash"],
                    size=entry["size"],
                )
                for entry in data["files"]
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid skill manifest at {manifest_uri}") from exc
        if not files:
            raise ValueError(f"Skill manifest at {manifest_uri} lists no files")
        return files

    async def _read_skill_file(self: Client, uri: str) -> bytes:
        contents = await self.read_resource(uri)
        if len(contents) != 1:
            raise ValueError(
                f"Expected one content block for {uri}, got {len(contents)}"
            )
        (content,) = contents
        if isinstance(content, mcp_types.TextResourceContents):
            return content.text.encode("utf-8")
        return base64.b64decode(content.blob)
