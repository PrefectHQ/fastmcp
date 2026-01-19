"""Basic skill provider for handling a single skill folder."""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyUrl

from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.skills._common import (
    SkillInfo,
    parse_frontmatter,
    scan_skill_files,
)
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)

# Ensure .md is recognized as text/markdown on all platforms (Windows may not have this)
mimetypes.add_type("text/markdown", ".md")


# -----------------------------------------------------------------------------
# Skill-specific Resource and ResourceTemplate subclasses
# -----------------------------------------------------------------------------


class SkillResource(Resource):
    """A resource representing a skill's main file or manifest."""

    skill_info: SkillInfo
    is_manifest: bool = False

    async def read(self) -> str | bytes | ResourceResult:
        """Read the resource content."""
        if self.is_manifest:
            return self._generate_manifest()
        else:
            main_file_path = self.skill_info.path / self.skill_info.main_file
            return main_file_path.read_text()

    def _generate_manifest(self) -> str:
        """Generate JSON manifest for the skill."""
        manifest = {
            "skill": self.skill_info.name,
            "files": [
                {"path": f.path, "size": f.size, "hash": f.hash}
                for f in self.skill_info.files
            ],
        }
        return json.dumps(manifest, indent=2)


class SkillFileTemplate(ResourceTemplate):
    """A template for accessing files within a skill."""

    skill_info: SkillInfo

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read a file from the skill directory."""
        file_path = arguments.get("path", "")
        full_path = self.skill_info.path / file_path

        # Security: ensure path doesn't escape skill directory
        try:
            full_path = full_path.resolve()
            if not full_path.is_relative_to(self.skill_info.path):
                raise ValueError(f"Path {file_path} escapes skill directory")
        except ValueError as e:
            raise ValueError(f"Invalid path: {e}") from e

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not full_path.is_file():
            raise ValueError(f"Not a file: {file_path}")

        # Determine if binary or text based on mime type
        mime_type, _ = mimetypes.guess_type(str(full_path))
        if mime_type and mime_type.startswith("text/"):
            return full_path.read_text()
        else:
            return full_path.read_bytes()

    async def _read(  # type: ignore[override]
        self,
        uri: str,
        params: dict[str, Any],
        task_meta: Any = None,
    ) -> ResourceResult:
        """Server entry point - read file directly without creating ephemeral resource.

        Note: task_meta is ignored - this template doesn't support background tasks.
        """
        # Call read() directly and convert to ResourceResult
        result = await self.read(arguments=params)
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource for the given URI and parameters.

        Note: This is not typically used since _read() handles file reading directly.
        Provided for compatibility with the ResourceTemplate interface.
        """
        file_path = params.get("path", "")
        full_path = (self.skill_info.path / file_path).resolve()

        # Security: ensure path doesn't escape skill directory
        if not full_path.is_relative_to(self.skill_info.path):
            raise ValueError(f"Path {file_path} escapes skill directory")

        mime_type, _ = mimetypes.guess_type(str(full_path))

        # Create a SkillFileResource that can read the file
        return SkillFileResource(
            uri=AnyUrl(uri),
            name=f"{self.skill_info.name}/{file_path}",
            description=f"File from {self.skill_info.name} skill",
            mime_type=mime_type or "application/octet-stream",
            skill_info=self.skill_info,
            file_path=file_path,
        )


class SkillFileResource(Resource):
    """A resource representing a specific file within a skill."""

    skill_info: SkillInfo
    file_path: str

    async def read(self) -> str | bytes | ResourceResult:
        """Read the file content."""
        full_path = self.skill_info.path / self.file_path

        # Security check
        full_path = full_path.resolve()
        if not full_path.is_relative_to(self.skill_info.path):
            raise ValueError(f"Path {self.file_path} escapes skill directory")

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")

        mime_type, _ = mimetypes.guess_type(str(full_path))
        if mime_type and mime_type.startswith("text/"):
            return full_path.read_text()
        else:
            return full_path.read_bytes()


# -----------------------------------------------------------------------------
# SkillProvider - handles a SINGLE skill folder
# -----------------------------------------------------------------------------


class SkillProvider(Provider):
    """Provider that exposes a single skill folder as MCP resources.

    Each skill folder must contain a main file (default: SKILL.md) and may
    contain additional supporting files.

    Exposes:
    - A Resource for the main file (skill://{name}/SKILL.md)
    - A Resource for the synthetic manifest (skill://{name}/_manifest)
    - Supporting files via ResourceTemplate or Resources (configurable)

    Args:
        skill_path: Path to the skill directory.
        main_file_name: Name of the main skill file. Defaults to "SKILL.md".
        supporting_files: How supporting files (everything except main file and
            manifest) are exposed to clients:
            - "template": Accessed via ResourceTemplate, hidden from list_resources().
              Clients discover files by reading the manifest first.
            - "resources": Each file exposed as individual Resource in list_resources().
              Full enumeration upfront.

    Example:
        ```python
        from pathlib import Path
        from fastmcp import FastMCP
        from fastmcp.server.providers.skills import SkillProvider

        mcp = FastMCP("My Skill")
        mcp.add_provider(SkillProvider(
            Path.home() / ".claude/skills/pdf-processing"
        ))
        ```
    """

    def __init__(
        self,
        skill_path: str | Path,
        main_file_name: str = "SKILL.md",
        supporting_files: Literal["template", "resources"] = "template",
    ) -> None:
        super().__init__()
        self._skill_path = Path(skill_path).resolve()
        self._main_file_name = main_file_name
        self._supporting_files = supporting_files
        self._skill_info: SkillInfo | None = None

        # Load at init to catch errors early
        self._load_skill()

    def _load_skill(self) -> None:
        """Load and parse the skill directory."""
        main_file = self._skill_path / self._main_file_name

        if not self._skill_path.exists():
            raise FileNotFoundError(f"Skill directory not found: {self._skill_path}")

        if not main_file.exists():
            raise FileNotFoundError(
                f"Main skill file not found: {main_file}. "
                f"Expected {self._main_file_name} in {self._skill_path}"
            )

        content = main_file.read_text()
        frontmatter, body = parse_frontmatter(content)

        # Get description from frontmatter or first non-empty line
        description = frontmatter.get("description", "")
        if not description:
            for line in body.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line[:200]
                    break
                elif line.startswith("#"):
                    description = line.lstrip("#").strip()[:200]
                    break

        # Scan all files in the skill directory
        files = scan_skill_files(self._skill_path)

        self._skill_info = SkillInfo(
            name=self._skill_path.name,
            description=description or f"Skill: {self._skill_path.name}",
            path=self._skill_path,
            main_file=self._main_file_name,
            files=files,
            frontmatter=frontmatter,
        )

        logger.debug(f"SkillProvider loaded skill: {self._skill_info.name}")

    @property
    def skill_info(self) -> SkillInfo:
        """Get the loaded skill info."""
        if self._skill_info is None:
            raise RuntimeError("Skill not loaded")
        return self._skill_info

    # -------------------------------------------------------------------------
    # Provider interface implementation
    # -------------------------------------------------------------------------

    async def _list_resources(self) -> Sequence[Resource]:
        """List skill resources."""
        skill = self.skill_info
        resources: list[Resource] = []

        # Main skill file
        resources.append(
            SkillResource(
                uri=AnyUrl(f"skill://{skill.name}/{self._main_file_name}"),
                name=f"{skill.name}/{self._main_file_name}",
                description=skill.description,
                mime_type="text/markdown",
                skill_info=skill,
                is_manifest=False,
            )
        )

        # Synthetic manifest
        resources.append(
            SkillResource(
                uri=AnyUrl(f"skill://{skill.name}/_manifest"),
                name=f"{skill.name}/_manifest",
                description=f"File listing for {skill.name}",
                mime_type="application/json",
                skill_info=skill,
                is_manifest=True,
            )
        )

        # If supporting_files="resources", add all supporting files as resources
        if self._supporting_files == "resources":
            for file_info in skill.files:
                # Skip main file and manifest (already added)
                if file_info.path == self._main_file_name:
                    continue

                mime_type, _ = mimetypes.guess_type(file_info.path)
                resources.append(
                    SkillFileResource(
                        uri=AnyUrl(f"skill://{skill.name}/{file_info.path}"),
                        name=f"{skill.name}/{file_info.path}",
                        description=f"File from {skill.name} skill",
                        mime_type=mime_type or "application/octet-stream",
                        skill_info=skill,
                        file_path=file_info.path,
                    )
                )

        return resources

    async def _get_resource(
        self, uri: str, version: VersionSpec | None = None
    ) -> Resource | None:
        """Get a resource by URI."""
        skill = self.skill_info

        # Parse URI: skill://{skill_name}/{file_path}
        if not uri.startswith("skill://"):
            return None

        path_part = uri[len("skill://") :]
        parts = path_part.split("/", 1)
        if len(parts) != 2:
            return None

        skill_name, file_path = parts
        if skill_name != skill.name:
            return None

        if file_path == "_manifest":
            return SkillResource(
                uri=AnyUrl(uri),
                name=f"{skill_name}/_manifest",
                description=f"File listing for {skill_name}",
                mime_type="application/json",
                skill_info=skill,
                is_manifest=True,
            )
        elif file_path == self._main_file_name:
            return SkillResource(
                uri=AnyUrl(uri),
                name=f"{skill_name}/{self._main_file_name}",
                description=skill.description,
                mime_type="text/markdown",
                skill_info=skill,
                is_manifest=False,
            )
        elif self._supporting_files == "resources":
            # Check if it's a known supporting file
            for file_info in skill.files:
                if file_info.path == file_path:
                    mime_type, _ = mimetypes.guess_type(file_path)
                    return SkillFileResource(
                        uri=AnyUrl(uri),
                        name=f"{skill_name}/{file_path}",
                        description=f"File from {skill_name} skill",
                        mime_type=mime_type or "application/octet-stream",
                        skill_info=skill,
                        file_path=file_path,
                    )

        return None

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        """List resource templates for accessing files within the skill."""
        # Only expose template if supporting_files="template"
        if self._supporting_files != "template":
            return []

        skill = self.skill_info
        return [
            SkillFileTemplate(
                uri_template=f"skill://{skill.name}/{{path*}}",
                name=f"{skill.name}_files",
                description=f"Access files within {skill.name}",
                mime_type="application/octet-stream",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                skill_info=skill,
            )
        ]

    async def _get_resource_template(
        self, uri: str, version: VersionSpec | None = None
    ) -> ResourceTemplate | None:
        """Get a resource template that matches the given URI."""
        # Only match if supporting_files="template"
        if self._supporting_files != "template":
            return None

        skill = self.skill_info

        if not uri.startswith("skill://"):
            return None

        path_part = uri[len("skill://") :]
        parts = path_part.split("/", 1)
        if len(parts) != 2:
            return None

        skill_name, file_path = parts
        if skill_name != skill.name:
            return None

        # Don't match known resources (main file, manifest)
        if file_path == "_manifest" or file_path == self._main_file_name:
            return None

        return SkillFileTemplate(
            uri_template=f"skill://{skill.name}/{{path*}}",
            name=f"{skill.name}_files",
            description=f"Access files within {skill.name}",
            mime_type="application/octet-stream",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            skill_info=skill,
        )

    def __repr__(self) -> str:
        return (
            f"SkillProvider(skill_path={self._skill_path!r}, "
            f"supporting_files={self._supporting_files!r})"
        )
