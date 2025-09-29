from mcp.types import ToolAnnotations

# MCP Mixin

This module provides the `MCPMixin` base class and associated decorators (`@mcp_tool`, `@mcp_resource`, `@mcp_prompt`).

It allows developers to easily define classes whose methods can be registered as tools, resources, or prompts with a `FastMCP` server instance using the `register_all()`, `register_tools()`, `register_resources()`, or `register_prompts()` methods provided by the mixin.

Includes support for
Tools:
* [enable/disable](https://gofastmcp.com/servers/tools#disabling-tools)
* [annotations](https://gofastmcp.com/servers/tools#annotations-2)
* [excluded arguments](https://gofastmcp.com/servers/tools#excluding-arguments)

Prompts:
* [enable/disable](https://gofastmcp.com/servers/prompts#disabling-prompts)

Resources:
* [enable/disable](https://gofastmcp.com/servers/resources#disabling-resources)
  
## Usage

Inherit from `MCPMixin` and use the decorators on the methods you want to register.

```python
from mcp.types import ToolAnnotations
from fastmcp import FastMCP
from fastmcp.contrib.mcp_mixin import MCPMixin, mcp_tool, mcp_resource, mcp_prompt

class MyComponent(MCPMixin):
    @mcp_tool(name="my_tool", description="Does something cool.")
    def tool_method(self):
        return "Tool executed!"

    # example of disabled tool
    @mcp_tool(name="my_tool", description="Does something cool.", enabled=False)
    def disabled_tool_method(self):
        # This function can't be called by client because it's disabled
        return "You'll never get here!"

    # example of excluded parameter tool
    @mcp_tool(
        name="my_tool", description="Does something cool.",
        enabled=False, exclude_args=['delete_everything'],
    )
    def excluded_param_tool_method(self, delete_everything=False):
        # MCP tool calls can't pass the "delete_everything" argument
        if delete_everything:
            return "Nothing to delete, I bet you're not a tool :)"
        return "You might be a tool if..."

    # example tool w/annotations
    @mcp_tool(
        name="my_tool", description="Does something cool.",
        annotations=ToolAnnotations(
            title="Attn LLM, use this tool first!",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )
    def tool_method(self):
        return "Tool executed!"

    # example tool w/everything
    @mcp_tool(
        name="my_tool", description="Does something cool.",
        enabled=True,
        exclude_args=['delete_all'],
        annotations=ToolAnnotations(
            title="Attn LLM, use this tool first!",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )
    def tool_method(self, delete_all=False):
        if delete_all:
            return "99 records deleted. I bet you're not a tool :)"
        return "Tool executed, but you might be a tool!"
    
    @mcp_resource(uri="component://data")
    def resource_method(self):
        return {"data": "some data"}

    # Disabled resource
    @mcp_resource(uri="component://data", enabled=False)
    def resource_method(self):
        return {"data": "some data"}

    # prompt
    @mcp_prompt(name="A prompt")
    def prompt_method(self, name):
        return f"What's up {name}?"

    # disabled prompt
    @mcp_prompt(name="A prompt", enabled=False)
    def prompt_method(self, name):
        return f"What's up {name}?"

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
