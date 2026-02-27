# Code Mode

CodeMode collapses an entire tool catalog into two meta-tools: `search` (keyword-based discovery) and `execute` (run Python scripts that chain tool calls in a sandbox). Instead of burning context tokens on every intermediate result, the LLM writes a script that runs server-side and returns only the final answer.

## Run

```bash
uv run python server.py   # in one terminal
uv run python client.py   # in another
```

## Example Output

```
══════════════════ CodeMode Transform ══════════════════

┌────────────── list_tools() ──────────────┐
│ Tool     Description                     │
│ search   Search for available tools ...  │
│ execute  Chain `await call_tool(...)` ... │
└── 8 backend tools collapsed into 2 ──────┘

┌──── search(query="math arithmetic") ─────┐
│ #   Tool      Description                │
│ 1   add       Add two numbers together.  │
│ 2   multiply  Multiply two numbers.      │
│ 3   fibonacci Generate the first n ...   │
└── 3 results ─────────────────────────────┘

┌────────────── execute ───────────────────┐
│ a = await call_tool("add", {"a": 3 ...  │
│ b = await call_tool("multiply", ...     │
│ return b                                 │
└── result: 14.0 ──────────────────────────┘
```

The key insight: with standard MCP, each `call_tool` is a round-trip through the LLM. With CodeMode, the LLM writes one script and all the tool calls happen server-side. Intermediate data never touches the context window.
