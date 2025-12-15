# Sampling Examples

These examples demonstrate FastMCP's sampling API, which allows server tools to request LLM completions from the client.

## Prerequisites

```bash
pip install fastmcp[anthropic]
export ANTHROPIC_API_KEY=your-key
```

Or run directly with `uv`:

```bash
uv run examples/sampling/text.py
```

## Examples

### Simple Text Sampling (`text.py`)

Basic sampling flow where a server tool requests an LLM completion:

```bash
uv run examples/sampling/text.py
```

### Structured Output (`structured_output.py`)

Uses `result_type` to get validated Pydantic models from the LLM:

```bash
uv run examples/sampling/structured_output.py
```

### Tool Use (`tool_use.py`)

Gives the LLM tools to use during sampling (calculator, time, dice):

```bash
uv run examples/sampling/tool_use.py
```

### Server Fallback (`server_fallback.py`)

Configures a fallback sampling handler on the server, enabling sampling even when clients don't support it:

```bash
uv run examples/sampling/server_fallback.py
```

## Using OpenAI Instead

To use OpenAI instead of Anthropic, change the handler:

```python
from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler

handler = OpenAISamplingHandler(default_model="gpt-4o-mini")
```

And install with `pip install fastmcp[openai]`.
