"""Skills plugin: expose agent skill folders as MCP resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin
from fastmcp.server.plugins.skills.directory_provider import SkillsDirectoryProvider
from fastmcp.server.plugins.skills.skill_provider import SkillProvider
from fastmcp.server.providers import Provider

# Vendor-name → list of skill-root paths. Captures the same preset
# paths the vendor subclasses (`ClaudeSkillsProvider`, `CursorSkillsProvider`,
# etc.) used to hardcode. The dict lets `Skills(SkillsConfig(vendor="claude"))`
# replace seven separate subclass names with one plugin + an enum value.
VENDOR_PATHS: dict[str, list[Path]] = {
    "claude": [Path.home() / ".claude" / "skills"],
    "cursor": [Path.home() / ".cursor" / "skills"],
    # VSCode and Copilot both resolve to ~/.copilot/skills in the pre-plugin
    # vendor subclasses; preserved verbatim for backcompat.
    "vscode": [Path.home() / ".copilot" / "skills"],
    "copilot": [Path.home() / ".copilot" / "skills"],
    "codex": [Path("/etc/codex/skills"), Path.home() / ".codex" / "skills"],
    "gemini": [Path.home() / ".gemini" / "skills"],
    "goose": [Path.home() / ".config" / "agents" / "skills"],
    "opencode": [Path.home() / ".config" / "opencode" / "skills"],
}

Vendor = Literal[
    "claude",
    "copilot",
    "codex",
    "cursor",
    "gemini",
    "goose",
    "opencode",
    "vscode",
]


class SkillsConfig(BaseModel):
    """Config model for the `Skills` plugin.

    Exactly one of `path`, `directory`, or `vendor` must be set. The
    check fires when the plugin builds its provider, not at config
    construction, so `SkillsConfig()` with no args still satisfies the
    plugin-framework's defaults-are-instantiable contract.
    """

    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    """Path to a single skill folder. Equivalent to the old
    `SkillProvider(path)` construction."""

    directory: str | list[str] | None = None
    """One or more directories to scan for skill subfolders. Equivalent
    to `SkillsDirectoryProvider(roots=...)`."""

    vendor: Vendor | None = None
    """Preset for a known vendor tool — resolves to that tool's
    conventional skills directory. Covers the set that the old
    `ClaudeSkillsProvider`, `CursorSkillsProvider`, etc. subclasses
    hardcoded."""

    reload: bool = False
    """Re-scan on each request. Useful in development; leave off in
    production where the skill catalog doesn't change."""

    main_file_name: str = "SKILL.md"
    """Name of the main file inside a skill folder."""

    supporting_files: Literal["template", "resources"] = "template"
    """How non-main files inside a skill folder are exposed.

    - `"template"`: accessed via a single `ResourceTemplate`, hidden
      from `list_resources()`.
    - `"resources"`: each file becomes its own `Resource` in
      `list_resources()`.
    """


class Skills(Plugin[SkillsConfig]):
    """Mount agent skill folders as MCP resources.

    One plugin covers all three entry points the pre-plugin API
    exposed as separate provider classes: single-folder,
    scan-a-directory, and vendor-preset.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.skills import Skills, SkillsConfig

        # Vendor preset — the common case:
        mcp = FastMCP(
            "skills",
            plugins=[Skills(SkillsConfig(vendor="claude"))],
        )

        # Custom directory:
        mcp = FastMCP(
            "skills",
            plugins=[Skills(SkillsConfig(directory="./skills"))],
        )

        # Single skill folder:
        mcp = FastMCP(
            "skills",
            plugins=[Skills(SkillsConfig(path="./skills/pdf-processing"))],
        )
        ```
    """

    def providers(self) -> list[Provider]:
        return [self._build_provider()]

    def _build_provider(self) -> Provider:
        sources_set = sum(
            bool(x)
            for x in (self.config.path, self.config.directory, self.config.vendor)
        )
        if sources_set == 0:
            raise ValueError(
                "SkillsConfig requires one of `path`, `directory`, or `vendor`."
            )
        if sources_set > 1:
            raise ValueError(
                "SkillsConfig requires exactly one of `path`, `directory`, or "
                "`vendor` — got multiple."
            )

        if self.config.path is not None:
            return SkillProvider(
                skill_path=self.config.path,
                main_file_name=self.config.main_file_name,
                supporting_files=self.config.supporting_files,
            )

        if self.config.vendor is not None:
            roots: Any = VENDOR_PATHS[self.config.vendor]
        else:
            # directory mode — accept str or list[str]
            assert self.config.directory is not None
            roots = (
                [self.config.directory]
                if isinstance(self.config.directory, str)
                else list(self.config.directory)
            )

        return SkillsDirectoryProvider(
            roots=roots,
            reload=self.config.reload,
            main_file_name=self.config.main_file_name,
            supporting_files=self.config.supporting_files,
        )
