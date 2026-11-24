"""List what a release changes about the installed surface, between two refs.

Usage (from the repository root):
    uv run .agents/skills/release/scripts/compat_diff.py v4.2.5 origin/main

Prints two sections for the release classification step:

- Dependencies: for each thing a user installs (`fastmcp`, `fastmcp[openai]`,
  `fastmcp-slim[server]`, ...), the third-party requirements it pulls in that
  were removed, added, or re-pinned. A removed requirement is no longer
  installed for users who got it from FastMCP, so their own `import` of it can
  fail after upgrading even though FastMCP's API did not change.
- Exports: names dropped from a public module's `__all__`, and public modules
  that no longer exist.

Every line needs a sentence in the release notes with what to do, or a short
reason it reaches no user. Read-only; it only runs `git`.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import tomllib
from collections.abc import Iterator

from packaging.requirements import Requirement

PYPROJECTS = [
    "pyproject.toml",
    "fastmcp_slim/pyproject.toml",
    "fastmcp_remote/pyproject.toml",
    "fastmcp_tasks/pyproject.toml",
]
SOURCE_ROOTS = ["fastmcp_slim/fastmcp", "fastmcp_remote", "fastmcp_tasks"]
HOOK = ("tool", "hatch", "metadata", "hooks", "uv-dynamic-versioning")

Graph = dict[str, dict[str, list[Requirement]]]  # package -> extra ("" = base) -> reqs


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def dig(data: dict, keys: tuple[str, ...]) -> dict:
    for key in keys:
        data = data.get(key, {})
    return data


def requirement(text: str) -> Requirement:
    return Requirement(text.replace("{{ version }}", "0"))


def graph(ref: str) -> Graph:
    packages: Graph = {}
    for path in PYPROJECTS:
        text = show(ref, path)
        if text is None:
            continue
        data = tomllib.loads(text)
        project, hook = data.get("project", {}), dig(data, HOOK)
        extras: dict[str, list[Requirement]] = {"": []}
        for source in (project, hook):
            for value in source.get("dependencies", []):
                extras[""].append(requirement(value))
            for extra, values in source.get("optional-dependencies", {}).items():
                extras.setdefault(extra, []).extend(requirement(v) for v in values)
        packages[project["name"]] = extras
    return packages


def installed(packages: Graph, name: str, extra: str) -> dict[str, str]:
    """Third-party requirements `pip install name[extra]` pulls in, by name."""
    found: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()
    todo = [(name, ""), (name, extra)]
    while todo:
        package, wanted = todo.pop()
        if (package, wanted) in seen:
            continue
        seen.add((package, wanted))
        for req in packages.get(package, {}).get(wanted, []):
            if req.marker is not None and "extra" in str(req.marker):
                continue
            if req.name in packages:
                todo.extend((req.name, e) for e in ["", *sorted(req.extras)])
            else:
                spec = str(req.specifier) or "any"
                if req.extras:
                    spec = f"[{','.join(sorted(req.extras))}] {spec}"
                if req.marker is not None:
                    spec += f"; {req.marker}"
                found.setdefault(req.name.lower(), set()).add(spec)
    return {dep: " and ".join(sorted(specs)) for dep, specs in found.items()}


def targets(before: Graph, after: Graph) -> Iterator[tuple[str, str]]:
    for package in sorted(set(before) | set(after)):
        extras = set(before.get(package, {})) | set(after.get(package, {}))
        for extra in sorted(extras):
            yield package, extra


def dependency_lines(prev: str, target: str) -> list[str]:
    before, after = graph(prev), graph(target)
    changes: dict[str, list[str]] = {}
    for package, extra in targets(before, after):
        label = f"`{package}[{extra}]`" if extra else f"`{package}`"
        old, new = installed(before, package, extra), installed(after, package, extra)
        for dep in set(old) | set(new):
            if dep not in new:
                change = f"`{dep}` ({old[dep]}) no longer installed by"
            elif dep not in old:
                change = f"`{dep}` ({new[dep]}) now installed by"
            elif old[dep] != new[dep]:
                change = f"`{dep}` {old[dep]} -> {new[dep]} in"
            else:
                continue
            changes.setdefault(change, []).append(label)
    return [
        f"- {change} {', '.join(labels)}" for change, labels in sorted(changes.items())
    ]


def public(path: str) -> bool:
    parts = path.removesuffix(".py").split("/")
    return path.endswith(".py") and not any(
        p.startswith("_") and p != "__init__" for p in parts
    )


def modules(ref: str) -> dict[str, str]:
    files = git("ls-tree", "-r", "--name-only", ref, "--", *SOURCE_ROOTS).split()
    return {path: show(ref, path) or "" for path in files if public(path)}


def exports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            try:
                return set(ast.literal_eval(node.value))
            except ValueError:
                return set()
    return set()


def module_name(path: str) -> str:
    path = re.sub(r"^fastmcp_slim/", "", path).removesuffix(".py")
    return path.removesuffix("/__init__").replace("/", ".")


def export_lines(prev: str, target: str) -> list[str]:
    old, new = modules(prev), modules(target)
    lines = []
    for path in sorted(old):
        if path not in new:
            lines.append(f"- module `{module_name(path)}` removed")
            continue
        dropped = exports(old[path]) - exports(new[path])
        if dropped:
            names = ", ".join(f"`{n}`" for n in sorted(dropped))
            lines.append(f"- `{module_name(path)}` no longer exports {names}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prev", help="last released tag on the line, e.g. v4.2.5")
    parser.add_argument("target", help="the ref being released, e.g. origin/main")
    args = parser.parse_args()

    for title, lines in (
        ("Dependencies", dependency_lines(args.prev, args.target)),
        ("Exports", export_lines(args.prev, args.target)),
    ):
        print(f"## {title}\n")
        print("\n".join(lines) if lines else "- no changes")
        print()


if __name__ == "__main__":
    main()
