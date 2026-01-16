# Persistent Session State

This example demonstrates session-scoped state that persists across tool calls within the same MCP session.

## What it shows

- State set in one tool call is readable in subsequent calls
- Different clients have isolated state (same keys, different values)
- Reconnecting creates a new session with fresh state

## Running

**HTTP transport:**

```bash
# Terminal 1: Start the server
uv run python server.py

# Terminal 2: Run the client
uv run python client.py
```

**STDIO transport (in-process):**

```bash
uv run python client_stdio.py
```

## Example output

```text
Each line below is a separate tool call

Alice connects
  session a9f6eaa3
  set user = Alice
  set secret = alice-password
  get user → Alice
  get secret → alice-password

Bob connects (different session)
  session 0c3bffc5
  get user → not found
  get secret → not found
  set user = Bob
  get user → Bob

Alice reconnects (new session)
  session e39640e3
  get user → not found
```
