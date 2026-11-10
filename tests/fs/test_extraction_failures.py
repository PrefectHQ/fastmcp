"""Component construction failures must remain local to the discovered file."""

from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers import FileSystemProvider
from fastmcp.server.providers.filesystem_discovery import (
    discover_and_import,
    import_module_from_file,
)


@pytest.fixture(
    params=[
        (
            "from fastmcp.tools import tool\n"
            "@tool\n"
            "def broken(*args: str) -> str:\n"
            "    return 'broken'\n",
            "*args",
        ),
        (
            "from fastmcp.prompts import prompt\n"
            "@prompt\n"
            "def broken(*args: str) -> str:\n"
            "    return 'broken'\n",
            "*args",
        ),
        (
            "from fastmcp.resources import resource\n"
            "@resource('data://{key}')\n"
            "def broken(other: str) -> str:\n"
            "    return other\n",
            "key",
        ),
    ],
    ids=["tool", "prompt", "resource-template"],
)
def invalid_component(request: pytest.FixtureRequest) -> tuple[str, str]:
    return request.param


def _write_tool(path: Path, name: str) -> None:
    path.write_text(
        "from fastmcp.tools import tool\n"
        "@tool\n"
        f"def {name}() -> str:\n"
        f"    return '{name}'\n"
    )


def test_discovery_records_extraction_failure(
    tmp_path: Path, invalid_component: tuple[str, str]
) -> None:
    source, error = invalid_component
    _write_tool(tmp_path / "a_valid.py", "first")
    broken_file = tmp_path / "b_invalid.py"
    _write_tool(broken_file, "a_partial")
    broken_file.write_text(broken_file.read_text() + source)
    _write_tool(tmp_path / "c_valid.py", "last")

    # Standalone decorators defer component validation until discovery.
    module = import_module_from_file(broken_file, provider_root=tmp_path)
    assert callable(module.broken)

    result = discover_and_import(tmp_path)

    assert [component.name for _, component in result.components] == ["first", "last"]
    assert set(result.failed_files) == {broken_file}
    assert error in result.failed_files[broken_file]


@pytest.mark.parametrize("reload", [False, True], ids=["startup", "reload"])
async def test_extraction_failure_keeps_other_tools_callable(
    tmp_path: Path, invalid_component: tuple[str, str], reload: bool
) -> None:
    source, error = invalid_component
    _write_tool(tmp_path / "a_valid.py", "healthy")
    broken_file = tmp_path / "b_invalid.py"
    if not reload:
        broken_file.write_text(source)
    provider = FileSystemProvider(tmp_path, reload=reload)
    if reload:
        # Introduce the invalid component after a successful initial scan.
        broken_file.write_text(source)

    server = FastMCP("Discovery failures", providers=[provider])
    async with Client(server) as client:
        assert [tool.name for tool in await client.list_tools()] == ["healthy"]
        assert (await client.call_tool("healthy")).data == "healthy"
        assert set(provider.failed_files) == {broken_file}
        assert error in provider.failed_files[broken_file]

        if reload:
            _write_tool(broken_file, "broken")
            assert {tool.name for tool in await client.list_tools()} == {
                "healthy",
                "broken",
            }
            assert (await client.call_tool("broken")).data == "broken"
            assert provider.failed_files == {}
