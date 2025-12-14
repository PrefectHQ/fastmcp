# Advanced Sampling Examples

These examples demonstrate FastMCP's sampling API with real LLM backends.

## Prerequisites

```bash
pip install fastmcp[openai]
export OPENAI_API_KEY=your-key
```

## Examples

### Structured Output (`structured_output.py`)

Uses `result_type` to get validated Pydantic models from the LLM:

```bash
python examples/advanced_sampling/structured_output.py
```

### Tool Use (`tool_use.py`)

Gives the LLM tools to use during sampling, with automatic tool execution:

```bash
python examples/advanced_sampling/tool_use.py
```

### Client Sampling Test (`client_sampling_test.py`)

Comprehensive test of advanced sampling features:
- Primitive `result_type` (`int`, `list[str]`) with automatic schema wrapping
- `sample_step()` for fine-grained loop control
- History tracking (verifies assistant messages are included)
- Multi-step reasoning with tools

```bash
python examples/advanced_sampling/client_sampling_test.py
```
