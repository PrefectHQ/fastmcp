---
title: Prompts
sidebarTitle: Prompts
description: Use server-side prompt templates with automatic argument serialization.
icon: message-lines
---

import { VersionBadge } from '/snippets/version-badge.mdx'

<VersionBadge version="2.0.0" />

Prompts are reusable message templates exposed by MCP servers. They can accept arguments to generate personalized message sequences for LLM interactions.

## Listing Prompts

Use `list_prompts()` to retrieve all available prompt templates:

```python
async with client:
    prompts = await client.list_prompts()
    # prompts -> list[mcp.types.Prompt]
    
    for prompt in prompts:
        print(f"Prompt: {prompt.name}")
        print(f"Description: {prompt.description}")
        if prompt.arguments:
            print(f"Arguments: {[arg.name for arg in prompt.arguments]}")
        # Access tags and other metadata
        if hasattr(prompt, '_meta') and prompt._meta:
            fastmcp_meta = prompt._meta.get('_fastmcp', {})
            print(f"Tags: {fastmcp_meta.get('tags', [])}")
```

### Filtering by Tags

<VersionBadge version="2.11.0" />

You can use the `meta` field to filter prompts based on their tags:

```python
async with client:
    prompts = await client.list_prompts()
    
    # Filter prompts by tag
    analysis_prompts = [
        prompt for prompt in prompts 
        if hasattr(prompt, '_meta') and prompt._meta and
           prompt._meta.get('_fastmcp', {}) and
           'analysis' in prompt._meta.get('_fastmcp', {}).get('tags', [])
    ]
    
    print(f"Found {len(analysis_prompts)} analysis prompts")
```

<Note>
The `_meta` field is part of the standard MCP specification. FastMCP servers include tags and other metadata within a `_fastmcp` namespace (e.g., `_meta._fastmcp.tags`) to avoid conflicts with user-defined metadata. This behavior can be controlled with the server's `include_fastmcp_meta` setting - when disabled, the `_fastmcp` namespace won't be included. Other MCP server implementations may not provide this metadata structure.
</Note>

## Using Prompts

### Basic Usage

Request a rendered prompt using `get_prompt()` with the prompt name and arguments:

```python
async with client:
    # Simple prompt without arguments
    result = await client.get_prompt("welcome_message")
    # result -> mcp.types.GetPromptResult
    
    # Access the generated messages
    for message in result.messages:
        print(f"Role: {message.role}")
        print(f"Content: {message.content}")
```

### Prompts with Arguments

Pass arguments as a dictionary to customize the prompt:

```python
async with client:
    # Prompt with simple arguments
    result = await client.get_prompt("user_greeting", {
        "name": "Alice",
        "role": "administrator"
    })
    
    # Access the personalized messages
    for message in result.messages:
        print(f"Generated message: {message.content}")
```

## Automatic Argument Serialization

<VersionBadge version="2.9.0" />

FastMCP automatically serializes complex arguments to JSON strings as required by the MCP specification. This allows you to pass typed objects directly:

```python
from dataclasses import dataclass

@dataclass
class UserData:
    name: str
    age: int

async with client:
    # Complex arguments are automatically serialized
    result = await client.get_prompt("analyze_user", {
        "user": UserData(name="Alice", age=30),     # Automatically serialized to JSON
        "preferences": {"theme": "dark"},           # Dict serialized to JSON string
        "scores": [85, 92, 78],                     # List serialized to JSON string
        "simple_name": "Bob"                        # Strings passed through unchanged
    })
```

The client handles serialization using `pydantic_core.to_json()` for consistent formatting. FastMCP servers can automatically deserialize these JSON strings back to the expected types.

### Serialization Examples

```python
async with client:
    result = await client.get_prompt("data_analysis", {
        # These will be automatically serialized to JSON strings:
        "config": {
            "format": "csv",
            "include_headers": True,
            "delimiter": ","
        },
        "filters": [
            {"field": "age", "operator": ">", "value": 18},
            {"field": "status", "operator": "==", "value": "active"}
        ],
        # This remains a string:
        "report_title": "Monthly Analytics Report"
    })
```

## Working with Prompt Results

The `get_prompt()` method returns a `GetPromptResult` object containing a list of messages:

```python
async with client:
    result = await client.get_prompt("conversation_starter", {"topic": "climate"})
    
    # Access individual messages
    for i, message in enumerate(result.messages):
        print(f"Message {i + 1}:")
        print(f"  Role: {message.role}")
        print(f"  Content: {message.content.text if hasattr(message.content, 'text') else message.content}")
```

## Raw MCP Protocol Access

For access to the complete MCP protocol objects, use the `*_mcp` methods:

```python
async with client:
    # Raw MCP method returns full protocol object
    prompts_result = await client.list_prompts_mcp()
    # prompts_result -> mcp.types.ListPromptsResult
    
    prompt_result = await client.get_prompt_mcp("example_prompt", {"arg": "value"})
    # prompt_result -> mcp.types.GetPromptResult
```

## Multi-Server Clients

When using multi-server clients, prompts are accessible without prefixing (unlike tools):

```python
async with client:  # Multi-server client
    # Prompts from any server are directly accessible
    result1 = await client.get_prompt("weather_prompt", {"city": "London"})
    result2 = await client.get_prompt("assistant_prompt", {"query": "help"})
```

## Common Prompt Patterns

### System Messages

Many prompts generate system messages for LLM configuration:

```python
async with client:
    result = await client.get_prompt("system_configuration", {
        "role": "helpful assistant",
        "expertise": "python programming"
    })
    
    # Typically returns messages with role="system"
    system_message = result.messages[0]
    print(f"System prompt: {system_message.content}")
```

### Conversation Templates

Prompts can generate multi-turn conversation templates:

```python
async with client:
    result = await client.get_prompt("interview_template", {
        "candidate_name": "Alice",
        "position": "Senior Developer"
    })
    
    # Multiple messages for a conversation flow
    for message in result.messages:
        print(f"{message.role}: {message.content}")
```

<Tip>
Prompt arguments and their expected types depend on the specific prompt implementation. Check the server's documentation or use `list_prompts()` to see available arguments for each prompt.
</Tip>