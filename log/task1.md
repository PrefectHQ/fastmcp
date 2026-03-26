# Task 1 — Fix Issue #3571

**Issue**: [Mounted background tasks bind CurrentFastMCP() and Context.fastmcp to the parent server instead of the mounted child](https://github.com/PrefectHQ/fastmcp/issues/3571)
**Reporter**: nkaras (Nick Karastamatis)
**Assignee**: chrisguidry (Chris Guidry)
**Labels**: bug, server

---

## Problem Description

When a task-enabled tool is defined on a child FastMCP server and mounted into a parent server,
background task execution (`task=True`) resolves the server context to the **parent** server
instead of the **child** server.

Affects:
- `CurrentFastMCP()` dependency injection
- `ctx.fastmcp` on an injected `Context`

Foreground execution is fine — this only manifests in background task mode (`task=True`).

---

## Reproduction Script

```python
import asyncio
from fastmcp import Client, Context, FastMCP
from fastmcp.dependencies import CurrentFastMCP

child = FastMCP("child")

@child.tool(name="whoami", task=True)
async def whoami(server=CurrentFastMCP()) -> str:
    return f"server name: {server.name}"

@child.tool(name="whoami_ctx", task=True)
async def whoami_ctx(ctx: Context) -> str:
    return f"context server: {ctx.fastmcp.name}"

parent = FastMCP("parent")
parent.mount(child, namespace="child")

async def demo():
    async with Client(parent) as client:
        result1 = await (await client.call_tool("child_whoami", {}, task=True)).result()
        result2 = await (await client.call_tool("child_whoami_ctx", {}, task=True)).result()
        print(result1.content[0].text)
        print(result2.content[0].text)

if __name__ == "__main__":
    asyncio.run(demo())
```

### Expected Output
```
server name: child
context server: child
```

### Actual Output
```
server name: parent
context server: parent
```

---

## Investigation Steps

### Step 1 — Reproduce the bug

- [ ] Run the reproduction script and confirm the bug
- [ ] Identify relevant source files

### Step 2 — Trace the root cause

- [ ] Find where `CurrentFastMCP` context var is set during background task execution
- [ ] Find where `Context.fastmcp` is bound
- [ ] Identify why the child server context is not propagated

### Step 3 — Implement fix

- [ ] Write failing test first (TDD approach per CLAUDE.md)
- [ ] Implement the fix
- [ ] Verify the test passes

### Step 4 — Run full test suite

- [ ] `uv run pytest -n auto`
- [ ] `uv run prek run --all-files`

### Step 5 — Open PR

- [ ] Create branch
- [ ] Push to fork
- [ ] Open PR referencing #3571

---

## Investigation Log

### 2026-03-26

#### Environment Setup
- Installed uv 0.11.1
- Ran `uv sync` — all dependencies installed
- Ran `uv run prek install` — pre-commit hooks active

#### Root Cause Analysis

**Call chain (background task mode):**

1. Parent server receives `call_tool("child_whoami", task=True)`
2. `mcp_operations.py` runs within parent's request context (`_current_server = parent`)
3. Finds `FastMCPProviderTool` → delegates to `child_server.call_tool("whoami", task_meta=...)`
4. `child_server.call_tool()` creates `Context(fastmcp=child)` → sets `_current_server = child`
5. Tool is found, `submit_to_docket()` is called — at this point `ctx.fastmcp = child` ✅
6. Task is queued; Docket worker later runs the tool function
7. **Worker context**: `_current_server` is the **parent server** (set by parent's `_docket_lifespan()`)
8. `_CurrentContext.__aenter__()` calls `get_server()` → returns **parent** ❌
9. `Context(fastmcp=parent)` created → `ctx.fastmcp` returns parent ❌

**Two independent code paths both broken:**
- `ctx: Context` → goes through `_CurrentContext.__aenter__()` → `get_server()` → parent
- `server: CurrentFastMCP = CurrentFastMCP()` → `_CurrentFastMCP.__aenter__()` → `_current_server.get()` → parent

#### Fix Implemented

**Pattern**: Mirror the existing `_task_sessions` registry.

**Files modified:**
- `src/fastmcp/server/dependencies.py`:
  - Added `_task_servers: dict[str, weakref.ref[FastMCP]] = {}` registry
  - Added `register_task_server(session_id, task_id, server)` function
  - Added `get_task_server(session_id, task_id)` function
  - Updated `_CurrentContext.__aenter__()`: try `get_task_server()` before `get_server()`
  - Updated `_CurrentFastMCP.__aenter__()`: check task registry before `_current_server.get()`
  - Added new functions to `__all__`

- `src/fastmcp/server/tasks/handlers.py`:
  - Call `register_task_server(session_id, server_task_id, ctx.fastmcp)` in `submit_to_docket()`
  - At submission time `ctx.fastmcp` is the child server (correct)

**Tests added to `tests/server/tasks/test_task_mount.py`:**
- `test_mounted_task_receives_server_dependency` — strengthened to assert child server identity
- `test_mounted_task_context_fastmcp_is_child` — new test for `ctx.fastmcp`

#### Test Results
- All 3 new/updated tests pass ✅
- All 40 tests in `test_task_mount.py` pass ✅
- All 272 tests in `tests/server/tasks/` pass ✅
- Full test suite: 5140 passed, 0 failed ✅
- `uv run prek run --all-files` clean ✅

