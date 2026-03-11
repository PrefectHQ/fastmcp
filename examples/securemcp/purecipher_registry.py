"""PureCipher Secured MCP Registry MVP example.

Run::

    uv run python examples/securemcp/purecipher_registry.py

Then visit:
    http://localhost:8000/registry
    http://localhost:8000/registry/health
    http://localhost:8000/registry/tools
    http://localhost:8000/security/health
"""

from __future__ import annotations

from purecipher import (
    CertificationLevel,
    DataClassification,
    DataFlowDeclaration,
    PermissionScope,
    PureCipherRegistry,
    ResourceAccessDeclaration,
    SecurityManifest,
    ToolCategory,
)

registry = PureCipherRegistry(
    "purecipher-registry",
    signing_secret="purecipher-development-secret",
)

manifest = SecurityManifest(
    tool_name="weather-lookup",
    version="1.0.0",
    author="demo-author",
    description="Fetch current weather for a city.",
    permissions={PermissionScope.NETWORK_ACCESS},
    data_flows=[
        DataFlowDeclaration(
            source="input.city",
            destination="output.forecast",
            classification=DataClassification.PUBLIC,
            description="City name goes to a weather API and returns a forecast.",
        )
    ],
    resource_access=[
        ResourceAccessDeclaration(
            resource_pattern="https://api.weather.example/*",
            access_type="read",
            description="Call weather provider endpoint.",
            classification=DataClassification.PUBLIC,
        )
    ],
    tags={"weather", "api"},
)

submission = registry.submit_tool(
    manifest,
    display_name="Weather Lookup",
    categories={ToolCategory.NETWORK, ToolCategory.UTILITY},
    source_url="https://github.com/purecipher/weather-lookup",
    tool_license="MIT",
    requested_level=CertificationLevel.BASIC,
)

if not submission.accepted:
    raise SystemExit(submission.reason)


@registry.tool()
def registry_status() -> dict[str, str]:
    """Return a lightweight status message."""
    return {"status": "purecipher registry online"}


if __name__ == "__main__":
    registry.run(transport="streamable-http")
