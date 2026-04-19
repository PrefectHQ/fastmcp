# Architecture Improvement TODOs

Issues identified during documentation writing. Candidates for GitHub Issues.

---

## High Priority

### 1. WebSocket Transport Implementation
**Content:** Implement WebSocket transport for low-latency bidirectional use cases

**Background:** The MCP spec supports WebSocket, but FastMCP only has HTTP-based transports. WebSocket is essential for:
- AI chatbot interactions requiring low latency
- Real-time bidirectional communication
- Scenarios where HTTP overhead is prohibitive

**Suggested modification path:**
1. Create `src/fastmcp/client/transports/websocket.py` extending `ClientTransport`
2. Implement server-side WebSocket support in `src/fastmcp/server/mixins/transport.py`
3. Use `websockets` library (pure Python, async) or `httpx-ws`

**Acceptance criteria:**
- `WebSocketTransport` class exists with working `connect_session()`
- Server supports `transport="websocket"` in `run()` and `http_app()`
- Passes same test suite as `StreamableHttpTransport`
- Documentation includes WebSocket in transport comparison

---

### 2. `ClientTransport` Base Class Contract Documentation
**Content:** Add contract section to `ClientTransport` docstring explaining guarantees

**Background:** The base class has a basic description of what transports do, but lacks clear contract for what implementations must guarantee.

**Suggested modification path:**
1. Update docstring in `src/fastmcp/client/transports/base.py:36-44`
2. Add sections for: Message Ordering, Message Boundaries, Backpressure Handling, Error Semantics
3. Reference this documentation in docstring

**Acceptance criteria:**
- `ClientTransport` docstring has a "Contract" section
- Each required guarantee is explicitly documented
- Custom transport implementers know exactly what to expect

---

## Medium Priority

### 3. Transport Type Naming Inconsistency
**Content:** Standardize transport naming between client and server

**Background:**
- Client side: `StdioTransport`, `SSETransport`, `StreamableHttpTransport`
- Server side: `"stdio"`, `"http"`, `"streamable-http"`, `"sse"`

Issue: `"http"` on server actually means `"streamable-http"`, which is confusing.

**Suggested modification path:**
1. Keep `"http"` as alias for backward compatibility
2. Add explicit `transport="streamable-http"` as primary
3. Update documentation to clarify the alias relationship
4. Consider deprecation warning for `"http"` in favor of explicit name

**Acceptance criteria:**
- Server accepts both `"http"` and `"streamable-http"`
- Documentation clearly states that `"http"` is an alias
- Code comments explain the naming history

---

### 4. `keep_alive` Parameter Behavior Documentation
**Content:** Clarify `keep_alive=True` default behavior in stdio transport

**Background:** `keep_alive` defaults to `True`, which means the subprocess persists across multiple client connections. This is a significant behavioral default that isn't obvious from the parameter name alone.

**Suggested modification path:**
1. Add detailed docstring to `StdioTransport.__init__`
2. Explain lifecycle: subprocess starts on first `connect_session()`, runs until explicit `close()`
3. Consider adding `persist_subprocess` as a more explicit alias

**Acceptance criteria:**
- `StdioTransport` docstring explains `keep_alive` lifecycle clearly
- Users understand that `keep_alive=True` (default) means subprocess stays alive
- `close()` method behavior is documented

---

### 5. `_is_session_dead()` Method Docstring
**Content:** Add docstring explaining what "dead" means and reliability tradeoffs

**Background:** The method checks both write and read streams, but there's no explanation of why both are checked or which is more reliable.

**Suggested modification path:**
1. Add docstring to `_is_session_dead()` in `src/fastmcp/client/transports/stdio.py:144-160`
2. Explain that read stream status is often a more reliable indicator on some platforms
3. Note that this is a heuristic, not a perfect indicator

**Acceptance criteria:**
- Method has a docstring explaining purpose
- Tradeoffs between checking read vs write streams are documented
- Heuristic nature is acknowledged

---

### 6. `FastMCPTransport` Context Manager Nesting Documentation
**Content:** Add reference to test that validates context manager ordering

**Background:** The in-memory transport requires precise context manager ordering (lifespan OUTER, task group INNER) to prevent deadlocks during teardown. This is documented in tests but not clearly in the main transport code.

**Suggested modification path:**
1. Update comment in `src/fastmcp/client/transports/memory.py:57-78`
2. Add reference to `tests/client/transports/test_memory_transport.py:15-56`
3. Explain the Docket Worker / fakeredis blocking issue

**Acceptance criteria:**
- Comment explains why the ordering matters
- Reference to regression test is included
- Docket Worker shutdown scenario is mentioned

---

### 7. Unified Transport Error Hierarchy
**Content:** Create a `TransportError` base class with specific subclasses

**Background:** Transports raise various exceptions:
- `ConnectionRefusedError`
- `TimeoutError`
- `ssl.SSLError`
- `httpx.HTTPStatusError`

Callers must catch many exception types to handle transport errors uniformly.

**Suggested modification path:**
1. Consider wrapping or subclassing existing exceptions under `MCPTransportError`
2. Add `MCPTransportConnectionError`, `MCPTransportTimeoutError`, `MCPTransportAuthError`
3. Ensure backward compatibility with existing exception handling

**Acceptance criteria:**
- All transport-originated errors can be caught with `except MCPTransportError`
- Specific subclasses exist for common error types
- No breaking changes to existing code

---

### 8. Transport Interface Compliance Test Suite
**Content:** Create a parametrized test suite verifying all transports meet the same contract

**Background:** Each transport has its own tests, but there's no unified test suite verifying all transports meet the same interface contract.

**Suggested modification path:**
1. Create `tests/client/transports/test_contract.py`
2. Define a set of tests that any transport must pass:
   - `connect_session()` yields valid `ClientSession`
   - Context manager properly cleans up on normal exit
   - Context manager properly cleans up on exception
   - `close()` is idempotent
   - Message ordering is preserved
3. Parametrize with all transport types (use in-memory for speed, stdio for subprocess, mock HTTP for network)

**Acceptance criteria:**
- Test file exists at `tests/client/transports/test_contract.py`
- Tests are parametrized across all transport types
- All existing transports pass the contract tests

---

## Low Priority

### 9. Positive Example: `StreamableHTTPASGIApp` Error Message
**Content:** No action needed - this is well-done

**Background:** The error message in `src/fastmcp/server/http.py:40-62` is an excellent example of developer experience done right. It:
- Detects a common mistake (running ASGI app without lifespan support)
- Provides a clear explanation of what went wrong
- Gives a concrete fix suggestion

**Suggested modification path:** None - this is a positive example to learn from.

**Acceptance criteria:** N/A - documentation complete
