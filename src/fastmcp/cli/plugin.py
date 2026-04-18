"""CLI commands for working with FastMCP plugins.

Currently exposes a single verb, ``fastmcp plugin manifest``, which
imports a plugin class and emits its manifest (metadata + config schema
+ entry point) as JSON. The manifest is the artifact downstream
consumers (Horizon, registries, CI tooling) ingest to discover and
configure the plugin without importing its module themselves.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from cyclopts import Parameter

from fastmcp.server.plugins import Plugin
from fastmcp.utilities.logging import get_logger

logger = get_logger("cli.plugin")

plugin_app = cyclopts.App(
    name="plugin",
    help="Work with FastMCP plugins.",
    default_parameter=Parameter(negative=()),
)


def _resolve_plugin_class(entry_point: str) -> type[Plugin]:
    """Import a plugin class from a ``module.path:ClassName`` spec."""
    if ":" not in entry_point:
        raise ValueError(
            f"Invalid plugin reference {entry_point!r}: "
            f"expected 'module.path:ClassName'"
        )
    module_path, class_name = entry_point.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(f"Could not import module {module_path!r}: {exc}") from exc

    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise AttributeError(
            f"Module {module_path!r} has no attribute {class_name!r}"
        ) from exc

    if not isinstance(cls, type) or not issubclass(cls, Plugin):
        raise TypeError(f"{entry_point!r} does not refer to a fastmcp.Plugin subclass")
    return cls


@plugin_app.command(name="manifest")
def manifest_command(
    entry_point: Annotated[
        str,
        Parameter(help="Plugin reference in 'module.path:ClassName' form."),
    ],
    output: Annotated[
        Path | None,
        Parameter(
            name=["--output", "-o"],
            help="Write manifest JSON to this path instead of stdout.",
        ),
    ] = None,
) -> None:
    """Emit a plugin's manifest as JSON.

    Imports the referenced plugin class and prints its manifest to stdout,
    or writes it to the path given by ``-o/--output``.
    """
    try:
        cls = _resolve_plugin_class(entry_point)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        logger.error(str(exc))
        sys.exit(1)

    manifest = cls.manifest()

    if output is None:
        print(json.dumps(manifest, indent=2, sort_keys=False))
        return

    output.write_text(json.dumps(manifest, indent=2, sort_keys=False))
    print(f"Wrote manifest for {cls.meta.name} to {output}")
