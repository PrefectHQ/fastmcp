---
title: LLM Sampling
sidebarTitle: Sampling
description: Request LLM text generation from the client or a configured provider through the MCP context.
icon: robot
---

import { VersionBadge } from '/snippets/version-badge.mdx'

<VersionBadge version="2.0.0" />

LLM sampling allows MCP tools to request LLM text generation based on provided messages. By default, sampling requests are sent to the client's LLM, but you can also configure a fallback handler or always use a specific LLM provider. This is useful when tools need to leverage LLM capabilities to process data, generate responses, or perform text-based analysis.

## Why Use LLM Sampling?

LLM sampling enables tools to:

- **Leverage AI capabilities**: Use the client's LLM for text generation and analysis
- **Offload complex reasoning**: Let the LLM handle tasks requiring natural language understanding
- **Generate dynamic content**: Create responses, summaries, or transformations based on data
- **Maintain context**: Use the same LLM instance that the user is already interacting with

### Basic Usage

Use `ctx.sample()` to request text generation from the client's LLM:

```python {14}
from fastmcp import FastMCP, Context

mcp = FastMCP("SamplingDemo")

@mcp.tool
async def analyze_sentiment(text: str, ctx: Context) -> dict:
    """Analyze the sentiment of text using the client's LLM."""
    prompt = f"""Analyze the sentiment of the following text as positive, negative, or neutral. 
    Just output a single word - 'positive', 'negative', or 'neutral'.
    
    Text to analyze: {text}"""
    
    # Request LLM analysis
    response = await ctx.sample(prompt)
    
    # Process the LLM's response
    sentiment = response.text.strip().lower()
    
    # Map to standard sentiment values
    if "positive" in sentiment:
        sentiment = "positive"
    elif "negative" in sentiment:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    
    return {"text": text, "sentiment": sentiment}
```

## Method Signature

<Card icon="code" title="Context Sampling Method">
<ResponseField name="ctx.sample" type="async method">
  Request text generation from the client's LLM
  
  <Expandable title="Parameters">
    <ResponseField name="messages" type="str | list[str | SamplingMessage]">
      A string or list of strings/message objects to send to the LLM
    </ResponseField>
    
    <ResponseField name="system_prompt" type="str | None" default="None">
      Optional system prompt to guide the LLM's behavior
    </ResponseField>
    
    <ResponseField name="temperature" type="float | None" default="None">
      Optional sampling temperature (controls randomness, typically 0.0-1.0)
    </ResponseField>
    
    <ResponseField name="max_tokens" type="int | None" default="512">
      Optional maximum number of tokens to generate
    </ResponseField>
    
    <ResponseField name="model_preferences" type="ModelPreferences | str | list[str] | None" default="None">
      Optional model selection preferences (e.g., model hint string, list of hints, or ModelPreferences object)
    </ResponseField>
  </Expandable>
  
  <Expandable title="Response">
    <ResponseField name="response" type="TextContent | ImageContent">
      The LLM's response content (typically TextContent with a .text attribute)
    </ResponseField>
  </Expandable>
</ResponseField>
</Card>

## Simple Text Generation

### Basic Prompting

Generate text with simple string prompts:

```python {6}
@mcp.tool
async def generate_summary(content: str, ctx: Context) -> str:
    """Generate a summary of the provided content."""
    prompt = f"Please provide a concise summary of the following content:\n\n{content}"
    
    response = await ctx.sample(prompt)
    return response.text
```

### System Prompt

Use system prompts to guide the LLM's behavior:

```python {4-9}
@mcp.tool
async def generate_code_example(concept: str, ctx: Context) -> str:
    """Generate a Python code example for a given concept."""
    response = await ctx.sample(
        messages=f"Write a simple Python code example demonstrating '{concept}'.",
        system_prompt="You are an expert Python programmer. Provide concise, working code examples without explanations.",
        temperature=0.7,
        max_tokens=300
    )
    
    code_example = response.text
    return f"```python\n{code_example}\n```"
```


### Model Preferences

Specify model preferences for different use cases:

```python {4-8, 17-22}
@mcp.tool
async def creative_writing(topic: str, ctx: Context) -> str:
    """Generate creative content using a specific model."""
    response = await ctx.sample(
        messages=f"Write a creative short story about {topic}",
        model_preferences="claude-3-sonnet",  # Prefer a specific model
        include_context="thisServer",  # Use the server's context
        temperature=0.9,  # High creativity
        max_tokens=1000
    )
    
    return response.text

@mcp.tool
async def technical_analysis(data: str, ctx: Context) -> str:
    """Perform technical analysis with a reasoning-focused model."""
    response = await ctx.sample(
        messages=f"Analyze this technical data and provide insights: {data}",
        model_preferences=["claude-3-opus", "gpt-4"],  # Prefer reasoning models
        temperature=0.2,  # Low randomness for consistency
        max_tokens=800
    )
    
    return response.text
```

### Complex Message Structures

Use structured messages for more complex interactions:

```python {1, 6-10}
from fastmcp.client.sampling import SamplingMessage

@mcp.tool
async def multi_turn_analysis(user_query: str, context_data: str, ctx: Context) -> str:
    """Perform analysis using multi-turn conversation structure."""
    messages = [
        SamplingMessage(role="user", content=f"I have this data: {context_data}"),
        SamplingMessage(role="assistant", content="I can see your data. What would you like me to analyze?"),
        SamplingMessage(role="user", content=user_query)
    ]
    
    response = await ctx.sample(
        messages=messages,
        system_prompt="You are a data analyst. Provide detailed insights based on the conversation context.",
        temperature=0.3
    )
    
    return response.text
```

## Sampling Fallback Handler

Client support for sampling is optional. If the client does not support sampling, the server will report an error indicating that the client does not support sampling.

However, you can provide a `sampling_handler` to the FastMCP server, which sends sampling requests directly to an LLM provider instead of routing through the client. The `sampling_handler_behavior` parameter controls when this handler is used:

- **`"fallback"`** (default): Uses the handler only when the client doesn't support sampling. Requests go to the client first, falling back to the handler if needed.
- **`"always"`**: Always uses the handler, bypassing the client entirely. Useful when you want full control over the LLM used for sampling.

Sampling handlers can be implemented using any LLM provider, but a sample implementation for OpenAI is provided as a Contrib module. Sampling lacks the full capabilities of typical LLM completions. For this reason, the OpenAI sampling handler, pointed at a third-party provider's OpenAI-compatible API, is often sufficient to implement a sampling handler.

### Fallback Mode (Default)

Uses the handler only when the client doesn't support sampling:

```python
import asyncio
import os

from mcp.types import ContentBlock
from openai import OpenAI

from fastmcp import FastMCP
from fastmcp.experimental.sampling.handlers.openai import OpenAISamplingHandler
from fastmcp.server.context import Context


async def async_main():
    server = FastMCP(
        name="OpenAI Sampling Fallback Example",
        sampling_handler=OpenAISamplingHandler(
            default_model="gpt-4o-mini",
            client=OpenAI(
                api_key=os.getenv("API_KEY"),
                base_url=os.getenv("BASE_URL"),
            ),
        ),
        sampling_handler_behavior="fallback",  # Default - only use when client doesn't support sampling
    )

    @server.tool
    async def test_sample_fallback(ctx: Context) -> ContentBlock:
        # Will use client's LLM if available, otherwise falls back to the handler
        return await ctx.sample(
            messages=["hello world!"],
        )

    await server.run_http_async()


if __name__ == "__main__":
    asyncio.run(async_main())
```

### Always Mode

Always uses the handler, bypassing the client:

```python
server = FastMCP(
    name="Server-Controlled Sampling",
    sampling_handler=OpenAISamplingHandler(
        default_model="gpt-4o-mini",
        client=OpenAI(api_key=os.getenv("API_KEY")),
    ),
    sampling_handler_behavior="always",  # Always use the handler, never the client
)

@server.tool
async def analyze_data(data: str, ctx: Context) -> str:
    # Will ALWAYS use the server's configured LLM, not the client's
    result = await ctx.sample(
        messages=f"Analyze this data: {data}",
        system_prompt="You are a data analyst.",
    )
    return result.text
```

## Client Requirements

By default, LLM sampling requires client support:

- Clients must implement sampling handlers to process requests (see [Client Sampling](/clients/sampling))
- If the client doesn't support sampling and no fallback handler is configured, `ctx.sample()` will raise an error
- Configure a `sampling_handler` with `sampling_handler_behavior="fallback"` to automatically handle clients that don't support sampling
- Use `sampling_handler_behavior="always"` to completely bypass the client and control which LLM is used
