"""Reload scans must discover a single current generation of source modules."""

import sys
from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.providers import FileSystemProvider


def write_tools(path: Path, generation: int, removed: bool, added: bool) -> None:
    source = (
        "from fastmcp.tools import tool\n"
        f"_VALUE = 'generation {generation}'\n"
        "@tool\ndef keep() -> str:\n"
        "    return _VALUE\n"
    )
    if removed:
        source += "@tool\ndef removed() -> str:\n    return 'removed'\n"
    if added:
        source += "@tool\ndef added() -> str:\n    return 'added'\n"
    path.write_text(source)


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("sibling", [False, True])
async def test_current_source_generation(
    tmp_path: Path, package: bool, sibling: bool
) -> None:
    root = tmp_path / "generation_components"
    root.mkdir()
    if package:
        (root / "__init__.py").write_text("")
    source = root / f"z_definitions_{tmp_path.name}.py"
    write_tools(source, 1, removed=True, added=False)
    if sibling:
        prefix = "." if package else ""
        (root / "a_importer.py").write_text(
            f"from {prefix}{source.stem} import removed\n"
        )
    provider = FileSystemProvider(root, reload=True)
    async with Client(FastMCP(providers=[provider])) as client:
        assert {t.name for t in await client.list_tools()} == {"keep", "removed"}
        assert (await client.call_tool("keep")).data == "generation 1"
        assert (await client.call_tool("removed")).data == "removed"

        write_tools(source, 2, removed=True, added=True)
        assert (await client.call_tool("keep")).data == "generation 2"
        assert {t.name for t in await client.list_tools()} == {
            "keep",
            "removed",
            "added",
        }
        assert (await client.call_tool("added")).data == "added"

        write_tools(source, 3, removed=False, added=True)
        assert (await client.call_tool("keep")).data == "generation 3"
        # Check invocation separately so stale callability is tested on the base.
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("removed")
        assert {t.name for t in await client.list_tools()} == {"keep", "added"}
        assert (await client.call_tool("keep")).data == "generation 3"
        assert (await client.call_tool("added")).data == "added"
        if sibling:
            assert root / "a_importer.py" in provider.failed_files


@pytest.mark.parametrize("package", [False, True])
@pytest.mark.parametrize("stable_first", [False, True])
async def test_overlapping_stable_provider(
    tmp_path: Path, package: bool, stable_first: bool
) -> None:
    root = tmp_path / "overlapping_components"
    root.mkdir()
    if package:
        (root / "__init__.py").write_text("")
    source = root / "definitions.py"
    write_tools(source, 1, removed=True, added=False)
    first = FileSystemProvider(root, reload=not stable_first)
    second = FileSystemProvider(root, reload=stable_first)
    stable, live = (first, second) if stable_first else (second, first)
    write_tools(source, 2, removed=False, added=True)
    async with (
        Client(FastMCP(providers=[live])) as live_client,
        Client(FastMCP(providers=[stable])) as stable_client,
    ):
        assert (await live_client.call_tool("keep")).data == "generation 2"
        assert {t.name for t in await live_client.list_tools()} == {"keep", "added"}
        assert (await stable_client.call_tool("keep")).data == "generation 1"
        assert (await stable_client.call_tool("removed")).data == "removed"
        assert {t.name for t in await stable_client.list_tools()} == {"keep", "removed"}


@pytest.mark.parametrize("package", [False, True])
async def test_dependencies_execute_once_per_generation(
    tmp_path: Path, package: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "dependency_components"
    root.mkdir()
    counter = tmp_path / "executions.txt"
    counter.write_text("")
    dependency_name = f"z_dependency_{tmp_path.name}"
    prefix = "." if package else ""
    if package:
        (root / "__init__.py").write_text("")
    # This module is imported by both earlier files, then scanned itself.
    dependency = root / f"{dependency_name}.py"
    dependency.write_text(
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as stream:\n"
        "    stream.write('execution\\n')\n"
        "VALUE = 'old'\n"
    )
    outside = tmp_path / f"external_{tmp_path.name}.py"
    outside.write_text("SINGLETON = object()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("a_first", "b_second"):
        (root / f"{name}.py").write_text(
            "from fastmcp.tools import tool\n"
            f"from {prefix}{dependency_name} import VALUE\n"
            f"import {outside.stem} as external\n"
            f"@tool\ndef {name}() -> str:\n    return VALUE\n"
        )
    # Lazy imports should resolve the new published generation too.
    (root / "lazy.py").write_text(
        "from fastmcp.tools import tool\n"
        "@tool\ndef lazy() -> str:\n"
        f"    from {prefix}{dependency_name} import VALUE\n"
        "    return VALUE\n"
    )
    provider = FileSystemProvider(root, reload=True)
    assert counter.read_text().splitlines() == ["execution"]
    external_module = sys.modules[outside.stem]
    singleton = external_module.SINGLETON
    dependency.write_text(dependency.read_text().replace("'old'", "'updated'"))
    async with Client(FastMCP(providers=[provider])) as client:
        for _ in range(3):
            assert (await client.call_tool("lazy")).data == "updated"
            assert counter.read_text().splitlines() == ["execution"] * (
                provider._reload_generation + 1
            )
            assert sys.modules[outside.stem] is external_module
            assert external_module.SINGLETON is singleton
        assert (await client.call_tool("a_first")).data == "updated"
        assert (await client.call_tool("b_second")).data == "updated"


async def test_removed_component_types(tmp_path: Path) -> None:
    source = tmp_path / "all_types.py"
    source.write_text(
        "from fastmcp.prompts import prompt\n"
        "from fastmcp.resources import resource\n"
        "@prompt\ndef removed_prompt() -> str:\n    return 'prompt'\n"
        "@resource('test://removed')\ndef removed_resource() -> str:\n"
        "    return 'resource'\n"
        "@resource('test://{name}')\ndef removed_template(name: str) -> str:\n"
        "    return name\n"
    )
    provider = FileSystemProvider(tmp_path, reload=True)
    assert len(await provider.list_prompts()) == 1
    assert len(await provider.list_resources()) == 1
    assert len(await provider.list_resource_templates()) == 1
    source.write_text("# All decorated definitions removed\n")
    assert await provider.list_prompts() == []
    assert await provider.list_resources() == []
    assert await provider.list_resource_templates() == []
    assert await provider.get_prompt("removed_prompt") is None
    assert await provider.get_resource("test://removed") is None
    assert await provider.get_resource_template("test://{name}") is None


@pytest.mark.parametrize("package", [False, True])
async def test_failed_generation_does_not_restore_old_components(
    tmp_path: Path, package: bool
) -> None:
    root = tmp_path / "recovering_components"
    root.mkdir()
    if package:
        (root / "__init__.py").write_text("")
    source = root / "definitions.py"
    write_tools(source, 1, removed=True, added=False)
    provider = FileSystemProvider(root, reload=True)
    async with Client(FastMCP(providers=[provider])) as client:
        assert (await client.call_tool("removed")).data == "removed"
        source.write_text(
            source.read_text() + "raise RuntimeError('broken generation')\n"
        )
        assert await client.list_tools() == []
        assert source.resolve() in provider.failed_files
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("keep")
        write_tools(source, 2, removed=False, added=True)
        assert (await client.call_tool("keep")).data == "generation 2"
        assert {t.name for t in await client.list_tools()} == {"keep", "added"}
        assert provider.failed_files == {}


async def test_scanned_symlink_target_is_refreshed(tmp_path: Path) -> None:
    root = tmp_path / "linked_components"
    root.mkdir()
    target = tmp_path / "linked_source.py"
    write_tools(target, 1, removed=True, added=False)
    (root / "component.py").symlink_to(target)
    provider = FileSystemProvider(root, reload=True)
    async with Client(FastMCP(providers=[provider])) as client:
        assert (await client.call_tool("removed")).data == "removed"
        write_tools(target, 2, removed=False, added=True)
        assert (await client.call_tool("keep")).data == "generation 2"
        assert {t.name for t in await client.list_tools()} == {"keep", "added"}
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("removed")
