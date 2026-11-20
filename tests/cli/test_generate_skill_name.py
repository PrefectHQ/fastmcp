"""Generated CLI skills must be accepted by Agent Skills readers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli import generate as generate_module
from fastmcp.cli.generate import (
    _derive_server_name,
    generate_cli_command,
    generate_skill_content,
)
from fastmcp.skills import Skill


@pytest.mark.parametrize(
    ("server_spec", "expected_name"),
    [
        ("https://weather.example.com/mcp", "weather-example-com-cli"),
        ("weather_api.py", "weather-api-cli"),
        ("My  Weather API", "my-weather-api-cli"),
        ("-weather-", "weather-cli"),
        ("weather--api", "weather-api-cli"),
        ("天气", "server-cli"),
        ("a" * 70, "a" * 60 + "-cli"),
        ("a" * 59 + "-weather", "a" * 59 + "-cli"),
        ("weather", "weather-cli"),
    ],
)
def test_generated_skill_name_is_valid(server_spec: str, expected_name: str) -> None:
    server_name = _derive_server_name(server_spec)
    content = generate_skill_content(server_name, "cli.py", [])

    skill = Skill.from_files({"SKILL.md": content})

    assert skill.name == expected_name
    assert f"# {server_name} CLI" in content


async def test_generate_cli_writes_loadable_skill(tmp_path: Path) -> None:
    server = FastMCP("Weather")

    @server.tool
    def forecast(city: str) -> str:
        return f"Sunny in {city}"

    output = tmp_path / "cli.py"
    # Only replace the external connection; discovery and generation stay real.
    with patch.object(generate_module, "_build_client", return_value=Client(server)):
        await generate_cli_command("https://weather.example.com/mcp", str(output))

    content = (tmp_path / "SKILL.md").read_bytes()
    skill = Skill.from_files({"SKILL.md": content})
    assert skill.name == "weather-example-com-cli"
    assert b"call-tool forecast" in content
    compile(output.read_text(encoding="utf-8"), str(output), "exec")
