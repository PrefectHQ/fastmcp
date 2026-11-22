from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path


def manifest(root: Path, *, source: bool) -> dict[str, tuple[int, str]]:
    files = {}
    for directory, names, leaves in os.walk(root, followlinks=False):
        parent = Path(directory)
        files[parent.relative_to(root).as_posix() + "/"] = (
            parent.stat().st_mode,
            "directory",
        )
        for name in list(names):
            path = parent / name
            if source and path == root / name and name in {".git", ".venv"}:
                names.remove(name)
            elif path.is_symlink():
                names.remove(name)
                leaves.append(name)
        for name in leaves:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    content = os.readlink(path).encode()
                elif stat.S_ISREG(mode):
                    with path.open("rb") as source_file:
                        fingerprint = hashlib.file_digest(
                            source_file, "sha256"
                        ).hexdigest()
                    files[relative] = (mode, fingerprint)
                    continue
                else:
                    content = b"special file"
                files[relative] = (mode, hashlib.sha256(content).hexdigest())
            except OSError:
                files[relative] = (0, "unreadable")
    return files


@dataclass
class VerificationInputs:
    repo: Path
    source: dict = field(init=False)
    installed: dict | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        self.source = manifest(self.repo, source=True)

    def installed_inputs(self) -> None:
        self.installed = manifest(self.repo / ".venv", source=False)

    def seal_metadata(self) -> None:
        if (self.repo / ".git").is_dir():
            self.metadata = manifest(self.repo / ".git", source=False)

    def changes(self) -> list[str]:
        changed = []
        for prefix, expected, actual in (
            ("", self.source, manifest(self.repo, source=True)),
            (
                ".git/",
                self.metadata,
                manifest(self.repo / ".git", source=False)
                if self.metadata is not None
                else None,
            ),
            (
                ".venv/",
                self.installed,
                manifest(self.repo / ".venv", source=False)
                if self.installed is not None
                else None,
            ),
        ):
            if expected is not None and actual is not None:
                changed.extend(
                    prefix + name
                    for name in expected.keys() | actual.keys()
                    if expected.get(name) != actual.get(name)
                )
        return sorted(changed)


def reject_mutations(result: dict, changes: list[str]) -> dict:
    if not changes:
        return result
    return {
        **result,
        "test_status": result.get("test_status", result["status"]),
        "status": "input_mutation",
        "reason": "Verification inputs changed during execution; discard transitions and repair the test inputs.",
        "input_changes": changes[:100],
        "input_changes_truncated": len(changes) > 100,
    }
