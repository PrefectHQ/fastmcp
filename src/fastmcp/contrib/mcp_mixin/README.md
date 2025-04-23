# MCP Mixin

This module provides the `MCPMixin` base class and associated decorators (`@mcp_tool`, `@mcp_resource`, `@mcp_prompt`).

It allows developers to easily define classes whose methods can be registered as tools, resources, or prompts with a `FastMCP` server instance using the `register_all()`, `register_tools()`, `register_resources()`, or `register_prompts()` methods provided by the mixin.

## Usage

Inherit from `MCPMixin` and use the decorators on the methods you want to register.

```python
from fastmcp import FastMCP
from fastmcp.contrib.mcp_mixin import MCPMixin, mcp_tool, mcp_resource

class MyComponent(MCPMixin):
    @mcp_tool(name="my_tool", description="Does something cool.")
    def tool_method(self):
        return "Tool executed!"

    @mcp_resource(uri="component://data")
    def resource_method(self):
        return {"data": "some data"}

mcp_server = FastMCP()
component = MyComponent()

# Register all decorated methods with a prefix
# Useful if you will have multiple instantiated objects of the same class
# and want to avoid name collisions.
component.register_all(mcp_server, prefix="my_comp") 

# Register without a prefix
# component.register_all(mcp_server) 

# Now 'my_comp_my_tool' tool and 'my_comp+component://data' resource are registered (if prefix used)
# Or 'my_tool' and 'component://data' are registered (if no prefix used)
```

The `prefix` argument in registration methods is optional. If omitted, methods are registered with their original decorated names/URIs. Individual separators (`tools_separator`, `resources_separator`, `prompts_separator`) can also be provided to `register_all` to change the separator for specific types.