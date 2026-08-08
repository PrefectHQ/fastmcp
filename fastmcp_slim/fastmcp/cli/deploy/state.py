"""Versioned JSON state helpers for the FastMCP CLI."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class StateFileError(RuntimeError):
    """A CLI state file could not be read or written safely."""


_WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$path = $args[0]
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User

if ([System.IO.Directory]::Exists($path)) {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit `
        -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
} else {
    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
}

$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $path -AclObject $acl
"""


def _restrict_windows_access(path: Path) -> None:
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _WINDOWS_ACL_SCRIPT,
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StateFileError("Could not restrict access to CLI state") from exc


def _restrict_access(path: Path, *, directory: bool = False) -> None:
    try:
        if os.name == "nt":
            _restrict_windows_access(path)
        else:
            path.chmod(0o700 if directory else 0o600)
    except OSError as exc:
        raise StateFileError("Could not restrict access to CLI state") from exc


def _prepare_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StateFileError("Could not create the CLI state directory") from exc
    _restrict_access(path, directory=True)


def read_state(
    path: Path,
    model: type[ModelT],
    *,
    secret: bool = False,
) -> ModelT | None:
    """Read and validate a versioned JSON state file."""
    if not path.exists():
        return None
    if path.is_symlink():
        raise StateFileError(f"CLI state must not be a symbolic link: {path.name}")

    if secret:
        _restrict_access(path.parent, directory=True)
        _restrict_access(path)

    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError):
        raise StateFileError(f"CLI state is invalid: {path.name}") from None
    except OSError as exc:
        raise StateFileError(f"Could not read CLI state: {path.name}") from exc


def write_state(path: Path, data: dict[str, Any]) -> None:
    """Write JSON through a restricted temporary file and atomic replacement."""
    _prepare_directory(path.parent)
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    descriptor: int | None = None
    temporary_path: Path | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)

        temporary_file = os.fdopen(descriptor, "wb")
        descriptor = None
        with temporary_file:
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        _restrict_access(temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None

        if os.name != "nt":
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except StateFileError:
        raise
    except OSError as exc:
        raise StateFileError(f"Could not write CLI state: {path.name}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def remove_state(path: Path) -> None:
    """Remove a state file when it exists."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise StateFileError(f"Could not remove CLI state: {path.name}") from exc
