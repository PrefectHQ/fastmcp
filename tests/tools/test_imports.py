"""Import regression tests for tools."""


def test_fastmcp_tools_imports_without_server_cycle():
    from fastmcp.tools import FunctionTool, tool

    assert FunctionTool.__name__ == "FunctionTool"
    assert callable(tool)
