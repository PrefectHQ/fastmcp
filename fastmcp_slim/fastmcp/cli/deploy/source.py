"""Resolve local source for a Horizon deployment."""

from __future__ import annotations

import ast
import json
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from fastmcp.cli.deploy.source_paths import (
    SourceInvalidError,
    relative_path,
    require_included_path,
    resolve_path,
)
from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource

GENERATED_REQUIREMENTS_PATH = ".fastmcp-deploy-requirements.txt"
_COMMON_ENTRYPOINTS = ("mcp", "server", "app")


@dataclass(frozen=True)
class DeploySource:
    """A resolved local source before archive creation."""

    source_root: Path
    entrypoint: str
    dependency_path: str | None
    generated_requirements: str | None = field(default=None, repr=False)
    required_paths: tuple[Path, ...] = field(default=(), repr=False)
    excluded_paths: tuple[Path, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class _DependencyInput:
    path: str | None
    generated_requirements: str | None
    required_paths: tuple[Path, ...]


async def resolve_deploy_source(server_spec: str | None) -> DeploySource:
    """Resolve a FastMCP server input without applying runtime settings."""
    config, source_root, config_path = _load_config(server_spec)
    source_path = resolve_path(
        source_root,
        config.source.path,
        label="Server source",
        file=True,
    )
    require_included_path(source_root, source_path, label="Server source")

    if config.source.entrypoint and ":" in config.source.entrypoint:
        raise SourceInvalidError(
            "Deployment entrypoints must use a file and one object name"
        )

    object_name = _resolve_entrypoint(source_path, config.source.entrypoint)
    relative_source = source_path.relative_to(source_root).as_posix()
    dependency = _resolve_dependencies(config, source_root, source_path)
    return DeploySource(
        source_root=source_root,
        entrypoint=f"{relative_source}:{object_name}",
        dependency_path=dependency.path,
        generated_requirements=dependency.generated_requirements,
        required_paths=(source_path, *dependency.required_paths),
        excluded_paths=(config_path,) if config_path is not None else (),
    )


def _resolve_entrypoint(source_path: Path, explicit_entrypoint: str | None) -> str:
    if explicit_entrypoint:
        return explicit_entrypoint

    try:
        with tokenize.open(source_path) as source_file:
            source_text = source_file.read()
        module = ast.parse(source_text, filename=str(source_path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise SourceInvalidError(
            f"The server source could not be parsed: {exc}"
        ) from exc

    candidates = _server_binding_names(module)
    if len(candidates) == 1:
        return candidates.pop()
    if candidates:
        raise SourceInvalidError(
            "Multiple possible server objects were found; provide an entrypoint"
        )
    raise SourceInvalidError(
        "No server object named mcp, server, or app was found; provide an entrypoint"
    )


def _server_binding_names(module: ast.Module) -> set[str]:
    candidates: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                bound_name = alias.asname or alias.name
                if bound_name in _COMMON_ENTRYPOINTS:
                    candidates.add(bound_name)
        elif isinstance(statement, ast.Assign) and _is_server_constructor(
            statement.value
        ):
            for target in statement.targets:
                candidates.update(_assigned_common_names(target))
        elif isinstance(statement, ast.AnnAssign) and _is_server_constructor(
            statement.value
        ):
            candidates.update(_assigned_common_names(statement.target))
        elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            if statement.name in _COMMON_ENTRYPOINTS and any(
                isinstance(child, ast.Return) and _is_server_constructor(child.value)
                for child in statement.body
            ):
                candidates.add(statement.name)
    return candidates


def _assigned_common_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name) and target.id in _COMMON_ENTRYPOINTS:
        return {target.id}
    return set()


def _is_server_constructor(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    function = value.func
    return _is_server_type_reference(function) or (
        isinstance(function, ast.Attribute)
        and _is_server_type_reference(function.value)
    )


def _is_server_type_reference(value: ast.expr) -> bool:
    return (isinstance(value, ast.Name) and value.id in {"FastMCP", "MCPServer"}) or (
        isinstance(value, ast.Attribute) and value.attr in {"FastMCP", "MCPServer"}
    )


def _load_config(
    server_spec: str | None,
) -> tuple[MCPServerConfig, Path, Path | None]:
    if server_spec is None:
        config_path = MCPServerConfig.find_config()
        if config_path is None:
            raise SourceInvalidError(
                "Provide a server input or add fastmcp.json to the current directory"
            )
        return _load_config_file(config_path)

    if server_spec.endswith(".json"):
        return _load_config_file(Path(server_spec))

    return (
        MCPServerConfig(source=FileSystemSource(path=server_spec)),
        Path.cwd().resolve(),
        None,
    )


def _load_config_file(config_path: Path) -> tuple[MCPServerConfig, Path, Path]:
    candidate = config_path.expanduser().absolute()
    source_root = candidate.parent.resolve()
    candidate = resolve_path(
        source_root,
        candidate,
        label="Server config",
        file=True,
    )
    return _read_config(candidate), source_root, candidate


def _read_config(config_path: Path) -> MCPServerConfig:
    try:
        return MCPServerConfig.from_file(config_path)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as exc:
        raise SourceInvalidError(
            f"The FastMCP configuration is invalid: {config_path}"
        ) from exc


def _resolve_dependencies(
    config: MCPServerConfig,
    source_root: Path,
    source_path: Path,
) -> _DependencyInput:
    environment = config.environment
    requirements = (
        resolve_path(
            source_root,
            environment.requirements,
            label="Requirements file",
            file=True,
        )
        if environment.requirements is not None
        else None
    )
    project = (
        resolve_path(
            source_root,
            environment.project,
            label="Environment project",
            directory=True,
        )
        if environment.project is not None
        else None
    )
    editable = tuple(
        resolve_path(
            source_root,
            path,
            label="Editable dependency",
            directory=True,
        )
        for path in (environment.editable or [])
    )

    for label, path in (
        ("Requirements file", requirements),
        ("Environment project", project),
        *(("Editable dependency", path) for path in editable),
    ):
        if path is not None:
            require_included_path(source_root, path, label=label)

    project_input = (
        (
            project,
            resolve_path(
                source_root,
                project / "pyproject.toml",
                label="Environment project pyproject.toml",
                file=True,
            ),
        )
        if project is not None
        else None
    )

    dependencies = sorted(set(environment.dependencies or []))
    needs_generated = bool(
        dependencies
        or editable
        or (project_input is not None and requirements is not None)
    )
    if needs_generated:
        lines: list[str] = []
        required_paths: list[Path] = []
        if requirements is not None:
            requirements_path = relative_path(source_root, requirements)
            if any(
                character in requirements_path for character in {"#", "\\", "\r", "\n"}
            ):
                raise SourceInvalidError(
                    "The requirements file path contains unsupported characters"
                )
            lines.append(f"-r {requirements_path}")
            required_paths.append(requirements)
        if project_input is not None:
            project, project_config = project_input
            lines.append(f"-e {_editable_requirement(source_root, project)}")
            required_paths.extend((project, project_config))
        for path in sorted(editable, key=lambda item: relative_path(source_root, item)):
            lines.append(f"-e {_editable_requirement(source_root, path)}")
            required_paths.append(path)
            editable_config = path / "pyproject.toml"
            if editable_config.is_file():
                required_paths.append(editable_config)
        lines.extend(dependencies)
        return _DependencyInput(
            path=GENERATED_REQUIREMENTS_PATH,
            generated_requirements="\n".join(lines) + "\n",
            required_paths=tuple(required_paths),
        )

    if requirements is not None:
        return _DependencyInput(
            path=relative_path(source_root, requirements),
            generated_requirements=None,
            required_paths=(requirements,),
        )

    if project_input is not None:
        project, project_config = project_input
        uv_lock = project / "uv.lock"
        if uv_lock.is_file():
            return _DependencyInput(
                path=relative_path(source_root, uv_lock),
                generated_requirements=None,
                required_paths=(project, uv_lock, project_config),
            )
        return _DependencyInput(
            path=relative_path(source_root, project_config),
            generated_requirements=None,
            required_paths=(project, project_config),
        )

    return _detect_dependency_input(source_root, source_path.parent)


def _editable_requirement(source_root: Path, path: Path) -> str:
    relative = relative_path(source_root, path)
    return f"file:{quote(relative, safe='/')}"


def _detect_dependency_input(source_root: Path, start: Path) -> _DependencyInput:
    current = start
    while True:
        dependency = _dependency_input_in(source_root, current)
        if dependency is not None:
            return dependency
        if current == source_root:
            return _DependencyInput(
                path=None,
                generated_requirements=None,
                required_paths=(),
            )
        current = current.parent


def _dependency_input_in(
    source_root: Path,
    directory: Path,
    *,
    include_requirements: bool = True,
) -> _DependencyInput | None:
    requirements = directory / "requirements.txt"
    if include_requirements and requirements.is_file():
        return _DependencyInput(
            path=relative_path(source_root, requirements),
            generated_requirements=None,
            required_paths=(requirements,),
        )

    uv_lock = directory / "uv.lock"
    pyproject = directory / "pyproject.toml"
    if uv_lock.is_file() and pyproject.is_file():
        return _DependencyInput(
            path=relative_path(source_root, uv_lock),
            generated_requirements=None,
            required_paths=(uv_lock, pyproject),
        )
    if pyproject.is_file():
        return _DependencyInput(
            path=relative_path(source_root, pyproject),
            generated_requirements=None,
            required_paths=(pyproject,),
        )
    return None
