"""Load CommandToolsSpec from disk."""

from __future__ import annotations

from pathlib import Path

from fastmcp.server.providers.command_config.models import (
    CommandToolsSpec,
    apply_working_dir_base,
    parse_command_tools_document,
)


def load_command_tools_spec(
    path: Path | str,
    *,
    resolve_relative_working_dirs: bool = True,
) -> CommandToolsSpec:
    """Load and validate a command-tools config from YAML or JSON.

    Args:
        path: Path to ``.yaml``, ``.yml``, or ``.json`` file.
        resolve_relative_working_dirs: If True, relative ``working_dir`` values are
            resolved against the config file's parent directory (not the process cwd).

    Raises:
        ValueError: Unknown file extension or parse errors surfaced as validation errors.
        pydantic.ValidationError: Invalid document structure.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    data = parse_command_tools_document(raw, suffix=p.suffix)
    spec = CommandToolsSpec.model_validate(data)
    if resolve_relative_working_dirs:
        spec = apply_working_dir_base(spec, p)
    return spec
