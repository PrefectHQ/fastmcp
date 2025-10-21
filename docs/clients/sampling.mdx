---
title: LLM Sampling
sidebarTitle: Sampling
description: Handle server-initiated LLM sampling requests.
icon: robot
---

import { VersionBadge } from "/snippets/version-badge.mdx";

<VersionBadge version="2.0.0" />

MCP servers can request LLM completions from clients. The client handles these requests through a sampling handler callback.

## Sampling Handler

Provide a `sampling_handler` function when creating the client:

```python
from fastmcp import Client
from fastmcp.client.sampling import (
    SamplingMessage,
    SamplingParams,
    RequestContext,
)

async def sampling_handler(
    messages: list[SamplingMessage],
    params: SamplingParams,
    context: RequestContext
) -> str:
    # Your LLM integration logic here
    # Extract text from messages and generate a response
    return "Generated response based on the messages"

client = Client(
    "my_mcp_server.py",
    sampling_handler=sampling_handler,
)
```

### Handler Parameters

The sampling handler receives three parameters:

<Card icon="code" title="Sampling Handler Parameters">
<ResponseField name="SamplingMessage" type="Sampling Message Object">
  <Expandable title="attributes">
    <ResponseField name="role" type='Literal["user", "assistant"]'>
      The role of the message.
    </ResponseField>

    <ResponseField name="content" type="TextContent | ImageContent | AudioContent">
      The content of the message.

      TextContent is most common, and has a `.text` attribute.
    </ResponseField>

  </Expandable>
</ResponseField>
<ResponseField name="SamplingParams" type="Sampling Parameters Object">
  <Expandable title="attributes">
    <ResponseField name="messages" type="list[SamplingMessage]">
      The messages to sample from
    </ResponseField>

    <ResponseField name="modelPreferences" type="ModelPreferences | None">
      The server's preferences for which model to select. The client MAY ignore
    these preferences.
    <Expandable title="attributes">
      <ResponseField name="hints" type="list[ModelHint] | None">
        The hints to use for model selection.
      </ResponseField>

      <ResponseField name="costPriority" type="float | None">
        The cost priority for model selection.
      </ResponseField>

      <ResponseField name="speedPriority" type="float | None">
        The speed priority for model selection.
      </ResponseField>

      <ResponseField name="intelligencePriority" type="float | None">
        The intelligence priority for model selection.
      </ResponseField>
    </Expandable>
    </ResponseField>

    <ResponseField name="systemPrompt" type="str | None">
      An optional system prompt the server wants to use for sampling.
    </ResponseField>

    <ResponseField name="includeContext" type="IncludeContext | None">
      A request to include context from one or more MCP servers (including the caller), to
      be attached to the prompt.
    </ResponseField>

    <ResponseField name="temperature" type="float | None">
      The sampling temperature.
    </ResponseField>

    <ResponseField name="maxTokens" type="int">
      The maximum number of tokens to sample.
    </ResponseField>

    <ResponseField name="stopSequences" type="list[str] | None">
      The stop sequences to use for sampling.
    </ResponseField>

    <ResponseField name="metadata" type="dict[str, Any] | None">
      Optional metadata to pass through to the LLM provider.
    </ResponseField>
    </Expandable>

</ResponseField>
<ResponseField name="RequestContext" type="Request Context Object">
  <Expandable title="attributes">
    <ResponseField name="request_id" type="RequestId">
      Unique identifier for the MCP request
    </ResponseField>
  </Expandable>
</ResponseField>
</Card>

## Basic Example

```python
from fastmcp import Client
from fastmcp.client.sampling import SamplingMessage, SamplingParams, RequestContext

async def basic_sampling_handler(
    messages: list[SamplingMessage],
    params: SamplingParams,
    context: RequestContext
) -> str:
    # Extract message content
    conversation = []
    for message in messages:
        content = message.content.text if hasattr(message.content, 'text') else str(message.content)
        conversation.append(f"{message.role}: {content}")

    # Use the system prompt if provided
    system_prompt = params.systemPrompt or "You are a helpful assistant."

    # Here you would integrate with your preferred LLM service
    # This is just a placeholder response
    return f"Response based on conversation: {' | '.join(conversation)}"

client = Client(
    "my_mcp_server.py",
    sampling_handler=basic_sampling_handler
)
```

<Note>
If the client doesn't provide a sampling handler, servers can optionally configure a fallback handler. See [Server Sampling](/servers/sampling#sampling-fallback-handler) for details.
</Note>