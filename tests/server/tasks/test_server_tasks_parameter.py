"""
Tests for server `tasks` parameter default inheritance.

Verifies that the server's `tasks` parameter correctly sets defaults for all
components (tools, prompts, resources), and that explicit component-level
settings properly override the server default.
"""

from fastmcp import FastMCP
from fastmcp.client import Client


async def test_server_tasks_true_defaults_all_components():
    """Server with tasks=True makes all components default to supporting tasks."""
    mcp = FastMCP("test", tasks=True)

    @mcp.tool()
    async def my_tool() -> str:
        return "tool result"

    @mcp.prompt()
    async def my_prompt() -> str:
        return "prompt result"

    @mcp.resource("test://resource")
    async def my_resource() -> str:
        return "resource result"

    async with Client(mcp) as client:
        # Verify all task-enabled components are registered with docket
        # Components use prefixed keys: tool:name, prompt:name, resource:uri
        docket = mcp.docket
        assert docket is not None
        assert "tool:my_tool@" in docket.tasks
        assert "prompt:my_prompt@" in docket.tasks
        assert "resource:test://resource@" in docket.tasks

        # Tool should support background execution
        tool_task = await client.call_tool("my_tool", task=True)
        assert not tool_task.returned_immediately

        # Prompt should support background execution
        prompt_task = await client.get_prompt("my_prompt", task=True)
        assert not prompt_task.returned_immediately

        # Resource should support background execution
        resource_task = await client.read_resource("test://resource", task=True)
        assert not resource_task.returned_immediately


async def test_server_tasks_false_defaults_all_components():
    """Server with tasks=False makes all components default to mode=forbidden."""
    import pytest
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("test", tasks=False)

    @mcp.tool()
    async def my_tool() -> str:
        return "tool result"

    @mcp.prompt()
    async def my_prompt() -> str:
        return "prompt result"

    @mcp.resource("test://resource")
    async def my_resource() -> str:
        return "resource result"

    async with Client(mcp) as client:
        # Tool with mode="forbidden" returns error when called with task=True
        tool_task = await client.call_tool("my_tool", task=True)
        assert tool_task.returned_immediately
        result = await tool_task.result()
        assert result.is_error
        assert "does not support task-augmented execution" in str(result)

        # Prompt with mode="forbidden" raises McpError when called with task=True
        with pytest.raises(McpError):
            await client.get_prompt("my_prompt", task=True)

        # Resource with mode="forbidden" raises McpError when called with task=True
        with pytest.raises(McpError):
            await client.read_resource("test://resource", task=True)


async def test_server_tasks_none_defaults_to_false():
    """Server with tasks=None (or omitted) defaults to False."""
    mcp = FastMCP("test")  # tasks=None, defaults to False

    @mcp.tool()
    async def my_tool() -> str:
        return "tool result"

    async with Client(mcp) as client:
        # Tool should NOT support background execution (mode="forbidden" from default)
        tool_task = await client.call_tool("my_tool", task=True)
        assert tool_task.returned_immediately
        result = await tool_task.result()
        assert result.is_error
        assert "does not support task-augmented execution" in str(result)


async def test_component_explicit_false_overrides_server_true():
    """Component with task=False overrides server default of tasks=True."""
    mcp = FastMCP("test", tasks=True)

    @mcp.tool(task=False)
    async def no_task_tool() -> str:
        return "immediate result"

    @mcp.tool()
    async def default_tool() -> str:
        return "background result"

    async with Client(mcp) as client:
        # Verify docket registration matches task settings (prefixed keys)
        docket = mcp.docket
        assert docket is not None
        assert (
            "tool:no_task_tool@" not in docket.tasks
        )  # task=False means not registered
        assert "tool:default_tool@" in docket.tasks  # Inherits tasks=True

        # Explicit False (mode="forbidden") returns error when called with task=True
        no_task = await client.call_tool("no_task_tool", task=True)
        assert no_task.returned_immediately
        result = await no_task.result()
        assert result.is_error
        assert "does not support task-augmented execution" in str(result)

        # Default should support background execution
        default_task = await client.call_tool("default_tool", task=True)
        assert not default_task.returned_immediately


async def test_component_explicit_true_overrides_server_false():
    """Component with task=True overrides server default of tasks=False."""
    mcp = FastMCP("test", tasks=False)

    @mcp.tool(task=True)
    async def task_tool() -> str:
        return "background result"

    @mcp.tool()
    async def default_tool() -> str:
        return "immediate result"

    async with Client(mcp) as client:
        # Verify docket registration matches task settings (prefixed keys)
        docket = mcp.docket
        assert docket is not None
        assert "tool:task_tool@" in docket.tasks  # task=True means registered
        assert "tool:default_tool@" not in docket.tasks  # Inherits tasks=False

        # Explicit True should support background execution despite server default
        task = await client.call_tool("task_tool", task=True)
        assert not task.returned_immediately

        # Default (mode="forbidden") returns error when called with task=True
        default = await client.call_tool("default_tool", task=True)
        assert default.returned_immediately
        result = await default.result()
        assert result.is_error


async def test_mixed_explicit_and_inherited():
    """Mix of explicit True/False/None on different components."""
    import pytest
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("test", tasks=True)  # Server default is True

    @mcp.tool()
    async def inherited_tool() -> str:
        return "inherits True"

    @mcp.tool(task=True)
    async def explicit_true_tool() -> str:
        return "explicit True"

    @mcp.tool(task=False)
    async def explicit_false_tool() -> str:
        return "explicit False"

    @mcp.prompt()
    async def inherited_prompt() -> str:
        return "inherits True"

    @mcp.prompt(task=False)
    async def explicit_false_prompt() -> str:
        return "explicit False"

    @mcp.resource("test://inherited")
    async def inherited_resource() -> str:
        return "inherits True"

    @mcp.resource("test://explicit_false", task=False)
    async def explicit_false_resource() -> str:
        return "explicit False"

    async with Client(mcp) as client:
        # Verify docket registration matches task settings
        # Components use prefixed keys: tool:name, prompt:name, resource:uri
        docket = mcp.docket
        assert docket is not None
        # task=True (explicit or inherited) means registered (with prefixed keys)
        assert "tool:inherited_tool@" in docket.tasks
        assert "tool:explicit_true_tool@" in docket.tasks
        assert "prompt:inherited_prompt@" in docket.tasks
        assert "resource:test://inherited@" in docket.tasks
        # task=False means NOT registered
        assert "tool:explicit_false_tool@" not in docket.tasks
        assert "prompt:explicit_false_prompt@" not in docket.tasks
        assert "resource:test://explicit_false@" not in docket.tasks

        # Tools
        inherited = await client.call_tool("inherited_tool", task=True)
        assert not inherited.returned_immediately

        explicit_true = await client.call_tool("explicit_true_tool", task=True)
        assert not explicit_true.returned_immediately

        # Explicit False (mode="forbidden") returns error
        explicit_false = await client.call_tool("explicit_false_tool", task=True)
        assert explicit_false.returned_immediately
        result = await explicit_false.result()
        assert result.is_error

        # Prompts
        inherited_prompt_task = await client.get_prompt("inherited_prompt", task=True)
        assert not inherited_prompt_task.returned_immediately

        # Explicit False prompt (mode="forbidden") raises McpError
        with pytest.raises(McpError):
            await client.get_prompt("explicit_false_prompt", task=True)

        # Resources
        inherited_resource_task = await client.read_resource(
            "test://inherited", task=True
        )
        assert not inherited_resource_task.returned_immediately

        # Explicit False resource (mode="forbidden") raises McpError
        with pytest.raises(McpError):
            await client.read_resource("test://explicit_false", task=True)


async def test_server_tasks_parameter_sets_component_defaults():
    """Server tasks parameter sets component defaults."""
    # Server tasks=True sets component defaults
    mcp = FastMCP("test", tasks=True)

    @mcp.tool()
    async def tool_inherits_true() -> str:
        return "tool result"

    async with Client(mcp) as client:
        # Tool inherits tasks=True from server
        tool_task = await client.call_tool("tool_inherits_true", task=True)
        assert not tool_task.returned_immediately

    # Server tasks=False sets component defaults
    mcp2 = FastMCP("test2", tasks=False)

    @mcp2.tool()
    async def tool_inherits_false() -> str:
        return "tool result"

    async with Client(mcp2) as client:
        # Tool inherits tasks=False (mode="forbidden") - returns error
        tool_task = await client.call_tool("tool_inherits_false", task=True)
        assert tool_task.returned_immediately
        result = await tool_task.result()
        assert result.is_error


async def test_resource_template_inherits_server_tasks_default():
    """Resource templates inherit server tasks default."""
    mcp = FastMCP("test", tasks=True)

    @mcp.resource("test://{item_id}")
    async def templated_resource(item_id: str) -> str:
        return f"resource {item_id}"

    async with Client(mcp) as client:
        # Template should support background execution
        resource_task = await client.read_resource("test://123", task=True)
        assert not resource_task.returned_immediately


async def test_multiple_components_same_name_different_tasks():
    """Different component types with same name can have different task settings."""
    import pytest
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("test", tasks=False)

    @mcp.tool(task=True)
    async def shared_name() -> str:
        return "tool result"

    @mcp.prompt()
    async def shared_name_prompt() -> str:
        return "prompt result"

    async with Client(mcp) as client:
        # Tool with explicit True should support background execution
        tool_task = await client.call_tool("shared_name", task=True)
        assert not tool_task.returned_immediately

        # Prompt inheriting False (mode="forbidden") raises McpError
        with pytest.raises(McpError):
            await client.get_prompt("shared_name_prompt", task=True)


async def test_task_with_custom_tool_name():
    """Tools with custom names work correctly as tasks (issue #2642).

    When a tool is registered with a custom name different from the function
    name, task execution should use the custom name for Docket lookup.
    """
    mcp = FastMCP("test", tasks=True)

    async def my_function() -> str:
        return "result from custom-named tool"

    mcp.tool(my_function, name="custom-tool-name")

    async with Client(mcp) as client:
        # Verify the tool is registered with its custom name in Docket (prefixed key)
        docket = mcp.docket
        assert docket is not None
        assert "tool:custom-tool-name@" in docket.tasks

        # Call the tool as a task using its custom name
        task = await client.call_tool("custom-tool-name", task=True)
        assert not task.returned_immediately
        result = await task
        assert result.data == "result from custom-named tool"


async def test_task_with_custom_resource_name():
    """Resources with custom names work correctly as tasks.

    Resources are registered/looked up by their .key (URI), not their name.
    """
    mcp = FastMCP("test", tasks=True)

    @mcp.resource("test://resource", name="custom-resource-name")
    async def my_resource_func() -> str:
        return "result from custom-named resource"

    async with Client(mcp) as client:
        # Verify the resource is registered with its key (prefixed URI) in Docket
        docket = mcp.docket
        assert docket is not None
        assert "resource:test://resource@" in docket.tasks

        # Call the resource as a task
        task = await client.read_resource("test://resource", task=True)
        assert not task.returned_immediately
        result = await task.result()
        assert result[0].text == "result from custom-named resource"


async def test_task_with_custom_template_name():
    """Resource templates with custom names work correctly as tasks.

    Templates are registered/looked up by their .key (uri_template), not their name.
    """
    mcp = FastMCP("test", tasks=True)

    @mcp.resource("test://{item_id}", name="custom-template-name")
    async def my_template_func(item_id: str) -> str:
        return f"result for {item_id}"

    async with Client(mcp) as client:
        # Verify the template is registered with its key (prefixed uri_template) in Docket
        docket = mcp.docket
        assert docket is not None
        assert "template:test://{item_id}@" in docket.tasks

        # Call the template as a task
        task = await client.read_resource("test://123", task=True)
        assert not task.returned_immediately
        result = await task.result()
        assert result[0].text == "result for 123"
