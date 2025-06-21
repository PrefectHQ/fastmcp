"""Tests for the CLI inspect command."""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from fastmcp.cli.cli import app


class TestInspectCommand:
    """Tests for the fastmcp inspect CLI command."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_inspect_basic_server(self):
        """Test inspecting a basic FastMCP 2.x server."""
        # Create a temporary server file
        server_content = '''
from fastmcp import FastMCP

mcp = FastMCP("TestServer", instructions="A test server")

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.resource("resource://data")
def get_data() -> str:
    """Get test data."""
    return "test data"

@mcp.prompt
def test_prompt(message: str) -> list:
    """Test prompt."""
    return [{"role": "user", "content": message}]
'''

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(server_content)
            server_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name

        try:
            # Run the inspect command
            result = self.runner.invoke(
                app, ["inspect", server_file, "-o", output_file]
            )

            assert result.exit_code == 0
            assert "✓ Inspected server: TestServer" in result.stdout
            assert "Tools: 1" in result.stdout
            assert "Prompts: 1" in result.stdout
            assert "Resources: 1" in result.stdout

            # Check the JSON output
            with open(output_file) as f:
                data = json.load(f)

            assert data["name"] == "TestServer"
            assert data["instructions"] == "A test server"
            assert "fastmcp_version" in data
            assert "mcp_version" in data
            assert "server_version" in data

            # Check tools
            assert len(data["tools"]) == 1
            tool = data["tools"][0]
            assert tool["key"] == "add"
            assert tool["name"] == "add"
            assert tool["description"] == "Add two numbers."
            assert "input_schema" in tool
            assert tool["enabled"] is True

            # Check resources
            assert len(data["resources"]) == 1
            resource = data["resources"][0]
            assert resource["key"] == "resource://data"
            assert resource["uri"] == "resource://data"
            assert resource["name"] == "get_data"

            # Check prompts
            assert len(data["prompts"]) == 1
            prompt = data["prompts"][0]
            assert prompt["key"] == "test_prompt"
            assert prompt["name"] == "test_prompt"
            assert prompt["description"] == "Test prompt."

            # Check capabilities
            assert "capabilities" in data
            assert "tools" in data["capabilities"]

        finally:
            # Clean up
            Path(server_file).unlink(missing_ok=True)
            Path(output_file).unlink(missing_ok=True)

    def test_inspect_with_object_spec(self):
        """Test inspecting a server with object specification."""
        server_content = '''
from fastmcp import FastMCP

server = FastMCP("ObjectSpecServer")

@server.tool
def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b
'''

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(server_content)
            server_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name

        try:
            # Run the inspect command with object specification
            result = self.runner.invoke(
                app, ["inspect", f"{server_file}:server", "-o", output_file]
            )

            assert result.exit_code == 0
            assert "✓ Inspected server: ObjectSpecServer" in result.stdout

            # Check the JSON output
            with open(output_file) as f:
                data = json.load(f)

            assert data["name"] == "ObjectSpecServer"
            assert len(data["tools"]) == 1
            assert data["tools"][0]["name"] == "multiply"

        finally:
            # Clean up
            Path(server_file).unlink(missing_ok=True)
            Path(output_file).unlink(missing_ok=True)

    def test_inspect_default_output(self):
        """Test inspecting with default output filename."""
        server_content = '''
from fastmcp import FastMCP

mcp = FastMCP("DefaultOutputServer")

@mcp.tool
def test_tool() -> str:
    """Test tool."""
    return "test"
'''

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(server_content)
            server_file = f.name

        try:
            # Run the inspect command without specifying output file
            result = self.runner.invoke(app, ["inspect", server_file])

            assert result.exit_code == 0
            assert "✓ Inspected server: DefaultOutputServer" in result.stdout
            assert "Report saved to: server-info.json" in result.stdout

            # Check the default output file exists
            default_output = Path("server-info.json")
            assert default_output.exists()

            # Check the JSON content
            with open(default_output) as f:
                data = json.load(f)

            assert data["name"] == "DefaultOutputServer"

        finally:
            # Clean up
            Path(server_file).unlink(missing_ok=True)
            Path("server-info.json").unlink(missing_ok=True)

    def test_inspect_invalid_server_file(self):
        """Test inspecting a non-existent server file."""
        result = self.runner.invoke(
            app, ["inspect", "nonexistent.py", "-o", "output.json"]
        )

        assert result.exit_code == 1
        # The error happens at the file parsing level, so no stdout output

    def test_inspect_server_with_error(self):
        """Test inspecting a server file with syntax errors."""
        server_content = """
from fastmcp import FastMCP

mcp = FastMCP("ErrorServer")
# Syntax error below
@mcp.tool
def broken_tool(
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(server_content)
            server_file = f.name

        try:
            result = self.runner.invoke(
                app, ["inspect", server_file, "-o", "output.json"]
            )

            assert result.exit_code == 1
            assert "✗ Failed to inspect server:" in result.stdout

        finally:
            # Clean up
            Path(server_file).unlink(missing_ok=True)
            Path("output.json").unlink(missing_ok=True)

    def test_inspect_comprehensive_json_structure(self):
        """Test that the JSON output has the correct structure."""
        server_content = '''
from fastmcp import FastMCP

mcp = FastMCP("ComprehensiveServer", instructions="Full test server")

@mcp.tool
def calculate(x: int, y: int) -> int:
    """Calculate something."""
    return x + y

@mcp.resource("resource://static")
def static_resource() -> str:
    """Static resource."""
    return "static"

@mcp.resource("resource://template/{id}")
def template_resource(id: str) -> str:
    """Template resource.""" 
    return f"data-{id}"

@mcp.prompt
def analysis_prompt(data: str) -> list:
    """Analysis prompt."""
    return [{"role": "user", "content": f"Analyze: {data}"}]
'''

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(server_content)
            server_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name

        try:
            result = self.runner.invoke(
                app, ["inspect", server_file, "-o", output_file]
            )

            assert result.exit_code == 0

            # Load and validate JSON structure
            with open(output_file) as f:
                data = json.load(f)

            # Check top-level structure
            required_fields = [
                "name",
                "instructions",
                "fastmcp_version",
                "mcp_version",
                "server_version",
                "tools",
                "prompts",
                "resources",
                "templates",
                "capabilities",
            ]
            for field in required_fields:
                assert field in data, f"Missing field: {field}"

            # Check version fields are strings
            assert isinstance(data["fastmcp_version"], str)
            assert isinstance(data["mcp_version"], str)
            assert isinstance(data["server_version"], str)

            # Check that we have the expected components
            assert len(data["tools"]) == 1
            assert len(data["resources"]) == 1
            assert len(data["templates"]) == 1
            assert len(data["prompts"]) == 1

            # Check tool structure
            tool = data["tools"][0]
            tool_fields = [
                "key",
                "name",
                "description",
                "input_schema",
                "annotations",
                "tags",
                "enabled",
            ]
            for field in tool_fields:
                assert field in tool, f"Missing tool field: {field}"

            # Check resource structure
            resource = data["resources"][0]
            resource_fields = [
                "key",
                "uri",
                "name",
                "description",
                "mime_type",
                "tags",
                "enabled",
            ]
            for field in resource_fields:
                assert field in resource, f"Missing resource field: {field}"

            # Check template structure
            template = data["templates"][0]
            template_fields = [
                "key",
                "uri_template",
                "name",
                "description",
                "mime_type",
                "tags",
                "enabled",
            ]
            for field in template_fields:
                assert field in template, f"Missing template field: {field}"

            # Check prompt structure
            prompt = data["prompts"][0]
            prompt_fields = [
                "key",
                "name",
                "description",
                "arguments",
                "tags",
                "enabled",
            ]
            for field in prompt_fields:
                assert field in prompt, f"Missing prompt field: {field}"

        finally:
            # Clean up
            Path(server_file).unlink(missing_ok=True)
            Path(output_file).unlink(missing_ok=True)
