# Bulk Tool Caller

This module provides the `BulkToolCaller` class, which extends the `MCPMixin` to offer tools for performing multiple tool calls in a single request to a FastMCP server. This can be useful for optimizing interactions with the server by reducing the overhead of individual tool calls.

## Usage

To use the `BulkToolCaller`, see the example [example.py](./example.py) file. The `BulkToolCaller` can be instantiated and then registered with a FastMCP server URL. It provides methods to call multiple tools in bulk, either different tools or the same tool with different arguments.


## Provided Tools

The `BulkToolCaller` provides the following tools:

### `call_tools_bulk`

Calls multiple different tools registered on the MCP server in a single request.

- **Arguments:**
    - `tool_calls` (list of `CallToolRequest`): A list of objects, where each object specifies the `tool` name and `arguments` for an individual tool call.
    - `continue_on_error` (bool, optional): If `True`, continue executing subsequent tool calls even if a previous one resulted in an error. Defaults to `True`.

- **Returns:**
    A list of `CallToolRequestResult` objects, each containing the result (`isError`, `content`) and the original `tool` name and `arguments` for each call.

### `call_tool_bulk`

Calls a single tool registered on the MCP server multiple times with different arguments in a single request.

- **Arguments:**
    - `tool` (str): The name of the tool to call.
    - `tool_arguments` (list of dict): A list of dictionaries, where each dictionary contains the arguments for an individual run of the tool.
    - `continue_on_error` (bool, optional): If `True`, continue executing subsequent tool calls even if a previous one resulted in an error. Defaults to `True`.

- **Returns:**
    A list of `CallToolRequestResult` objects, each containing the result (`isError`, `content`) and the original `tool` name and `arguments` for each call.