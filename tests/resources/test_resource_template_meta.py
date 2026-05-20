from mcp.types import Annotations, Icon

from fastmcp import FastMCP
from fastmcp.resources import ResourceTemplate
from fastmcp.server.providers.fastmcp_provider import (
    FastMCPProvider,
    FastMCPProviderResourceTemplate,
)


class TestResourceTemplateMeta:
    """Test ResourceTemplate meta functionality."""

    def test_template_meta_parameter(self):
        """Test that meta parameter is properly handled."""

        def template_func(param: str) -> str:
            return f"Result: {param}"

        meta_data = {"version": "2.0", "template": "test"}
        template = ResourceTemplate.from_function(
            fn=template_func,
            uri_template="test://{param}",
            name="test_template",
            meta=meta_data,
        )

        assert template.meta == meta_data
        mcp_template = template.to_mcp_template()
        # MCP template includes fastmcp meta, so check that our meta is included
        assert mcp_template.meta is not None
        assert meta_data.items() <= mcp_template.meta.items()


class TestResourceTemplateFieldPreservation:
    """Regression for #4061: annotations/meta/title/icons must survive
    materialization of a Resource from a ResourceTemplate."""

    def _template(self) -> ResourceTemplate:
        def fn(param: str) -> str:
            return f"value-{param}"

        return ResourceTemplate.from_function(
            fn=fn,
            uri_template="data://{param}",
            name="t",
            title="Human Title",
            meta={"owner": "team-a"},
            icons=[Icon(src="https://example.com/icon.png", mimeType="image/png")],
            annotations=Annotations(priority=0.5, audience=["user"]),
        )

    async def test_function_template_create_resource_preserves_all_fields(self):
        template = self._template()
        resource = await template.create_resource("data://x", {"param": "x"})

        assert resource.title == "Human Title"
        assert resource.meta == {"owner": "team-a"}
        assert resource.annotations is not None
        assert resource.annotations.priority == 0.5
        assert resource.annotations.audience == ["user"]
        assert resource.icons is not None
        assert len(resource.icons) == 1
        assert str(resource.icons[0].src) == "https://example.com/icon.png"

    async def test_created_resource_meta_is_not_aliased_to_template(self):
        """Mutating the materialized resource's meta must not bleed back into
        the template (each materialization must be independent)."""
        template = self._template()
        resource = await template.create_resource("data://x", {"param": "x"})

        assert resource.meta is not None
        resource.meta["mutated"] = True
        assert "mutated" not in (template.meta or {})

    async def test_fastmcp_provider_template_preserves_fields_without_double_wrap(
        self,
    ):
        """The FastMCPProvider path wraps template.get_meta() (already
        namespaced). Re-materializing must not nest fastmcp under fastmcp."""
        sub = FastMCP("sub")

        @sub.resource(
            "data://{param}",
            title="Sub Title",
            meta={"owner": "team-b"},
            icons=[Icon(src="https://example.com/s.png", mimeType="image/png")],
            annotations=Annotations(priority=0.9),
            tags={"alpha"},
        )
        def fn(param: str) -> str:
            return f"sub-{param}"

        provider = FastMCPProvider(sub)
        templates = await provider._list_resource_templates()
        assert len(templates) == 1
        wrapped = templates[0]
        assert isinstance(wrapped, FastMCPProviderResourceTemplate)

        resource = await wrapped.create_resource("data://x", {"param": "x"})

        assert resource.title == "Sub Title"
        assert resource.annotations is not None
        assert resource.annotations.priority == 0.9
        assert resource.icons is not None and len(resource.icons) == 1

        mcp_resource = resource.to_mcp_resource()
        assert mcp_resource.meta is not None
        # User meta survives end-to-end.
        assert mcp_resource.meta.get("owner") == "team-b"
        # Exactly one fastmcp namespace, and it is NOT double-wrapped.
        fastmcp_ns = mcp_resource.meta.get("fastmcp")
        assert isinstance(fastmcp_ns, dict)
        assert "fastmcp" not in fastmcp_ns
        assert "alpha" in fastmcp_ns.get("tags", [])
