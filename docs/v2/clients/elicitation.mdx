---
title: User Elicitation
sidebarTitle: Elicitation
description: Handle server-initiated user input requests with structured schemas.
icon: message-question
---

import { VersionBadge } from "/snippets/version-badge.mdx";

<VersionBadge version="2.10.0" />

## What is Elicitation?

Elicitation allows MCP servers to request structured input from users during tool execution. Instead of requiring all inputs upfront, servers can interactively ask users for information as needed - like prompting for missing parameters, requesting clarification, or gathering additional context.

For example, a file management tool might ask "Which directory should I create?" or a data analysis tool might request "What date range should I analyze?"

## How FastMCP Makes Elicitation Easy

FastMCP's client provides a helpful abstraction layer that:

- **Converts JSON schemas to Python types**: The raw MCP protocol uses JSON schemas, but FastMCP automatically converts these to Python dataclasses
- **Provides structured constructors**: Instead of manually building dictionaries that match the schema, you get dataclass constructors that ensure correct structure
- **Handles type conversion**: FastMCP takes care of converting between JSON representations and Python objects
- **Runtime introspection**: You can inspect the generated dataclass fields to understand the expected structure

When you implement an elicitation handler, FastMCP gives you a dataclass type that matches the server's schema, making it easy to create properly structured responses without having to manually parse JSON schemas.

## Elicitation Handler

Provide an `elicitation_handler` function when creating the client. FastMCP automatically converts the server's JSON schema into a Python dataclass type, making it easy to construct the response:

```python
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

async def elicitation_handler(message: str, response_type: type, params, context):
    # Present the message to the user and collect input
    user_input = input(f"{message}: ")
    
    # Create response using the provided dataclass type
    # FastMCP converted the JSON schema to this Python type for you
    response_data = response_type(value=user_input)
    
    # You can return data directly - FastMCP will implicitly accept the elicitation
    return response_data
    
    # Or explicitly return an ElicitResult for more control
    # return ElicitResult(action="accept", content=response_data)

client = Client(
    "my_mcp_server.py",
    elicitation_handler=elicitation_handler,
)
```

### Handler Parameters

The elicitation handler receives four parameters:

<Card icon="code" title="Elicitation Handler Parameters">
<ResponseField name="message" type="str">
  The prompt message to display to the user
</ResponseField>

<ResponseField name="response_type" type="type">
  A Python dataclass type that FastMCP created from the server's JSON schema. Use this to construct your response with proper typing and IDE support. If the server requests an empty object (indicating no response), this will be `None`.
</ResponseField>

<ResponseField name="params" type="ElicitRequestParams">
  The original MCP elicitation request parameters, including the raw JSON schema in `params.requestedSchema` if you need it
</ResponseField>

<ResponseField name="context" type="RequestContext">
  Request context containing metadata about the elicitation request
</ResponseField>
</Card>

### Response Actions

The handler can return data directly (which implicitly accepts the elicitation) or an `ElicitResult` object for more control over the response action:

<Card icon="code" title="ElicitResult Structure">
<ResponseField name="action" type="Literal['accept', 'decline', 'cancel']">
  How the user responded to the elicitation request
</ResponseField>

<ResponseField name="content" type="dataclass instance | dict | None">
  The user's input data (required for "accept", omitted for "decline"/"cancel")
</ResponseField>
</Card>

**Action Types:**
- **`accept`**: User provided valid input - include their data in the `content` field
- **`decline`**: User chose not to provide the requested information - omit `content`  
- **`cancel`**: User cancelled the entire operation - omit `content`

## Basic Example

```python
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

async def basic_elicitation_handler(message: str, response_type: type, params, context):
    print(f"Server asks: {message}")
    
    # Simple text input for demonstration
    user_response = input("Your response: ")
    
    if not user_response:
        # For non-acceptance, use ElicitResult explicitly
        return ElicitResult(action="decline")
    
    # Use the response_type dataclass to create a properly structured response
    # FastMCP handles the conversion from JSON schema to Python type
    # Return data directly - FastMCP will implicitly accept the elicitation
    return response_type(value=user_response)

client = Client(
    "my_mcp_server.py", 
    elicitation_handler=basic_elicitation_handler
)
```


