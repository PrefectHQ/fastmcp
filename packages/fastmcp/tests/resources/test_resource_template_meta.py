from fastmcp.resources import ResourceTemplate


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
