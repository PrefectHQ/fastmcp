# downstream smoke

Checks that FastMCP still works the way its consumers use it. The
[workflow](../../.github/workflows/run-downstream-smoke.yml) builds FastMCP's
wheels once, installs them where each consumer runs, and drives the consumer
against servers started from the checkout.

| job | what it runs | FastMCP install |
|---|---|---|
| pydantic-ai test suite | pydantic-ai's own MCP tests at a pinned tag | wheels overlaid on pydantic-ai's locked environment, as their FastMCP 4 job does |
| langchain test suite | `langchain.mcp`'s own unit and integration tests at a pinned tag | wheels overlaid on langchain's locked test environment |
| pydantic-ai smoke | `smoke_pydantic_ai.py` | client-only `fastmcp-slim[client]`, as `pydantic-ai-slim[mcp]` installs it |
| langchain.mcp smoke | `smoke_langchain_mcp.py`, including a `ClientGroup` of a legacy HTTP server and a modern stdio server | `fastmcp`, as `langchain[mcp]` installs it |
| langchain-mcp-adapters smoke | `smoke_langchain_mcp_adapters.py` | none: the adapters pin `mcp<2`, so they reach FastMCP over the wire |

Each smoke runs its checks over stdio, streamable HTTP, SSE, and a FastMCP proxy
in front of a stdio server, all with bearer auth where the transport allows it.
The checks cover shapes past releases got wrong: template literals such as `|`
and non-ASCII, comma-joined and exploded list query params, elicitation in both
protocol eras, schema counts written as floats, and a timed-out call followed by
another on the same connection.

Each smoke prints one line per check, writes a table to the job summary, and
reports warnings raised in the consumer's process. A check listed in
`_harness.py`'s `PROXY_GAPS` or `LEGACY_PROXY_GAPS` is a gap the proxy already
had in 4.0.5: it shows as ⚠ without failing the run, and fails the run if it
starts passing so the entry gets removed.

Pushes and PRs test the versions pinned in the workflow. The nightly run, or a
manual run with `latest`, tests each consumer's latest release.

## running locally

```bash
uv sync
uv build --wheel --out-dir dist . fastmcp_slim
slim="fastmcp-slim @ file://$PWD/$(ls dist/fastmcp_slim-*.whl)"
full="fastmcp @ file://$PWD/$(ls dist/fastmcp-4*.whl)"

uv run --isolated --no-project --no-config \
    --with 'pydantic-ai-slim[mcp]' --with "${slim/fastmcp-slim/fastmcp-slim[client]}" \
    python tests/downstream/smoke_pydantic_ai.py

uv run --isolated --no-project --no-config \
    --with 'langchain[mcp]' --with "$full" --with "$slim" \
    python tests/downstream/smoke_langchain_mcp.py

uv run --isolated --no-project --no-config \
    --with langchain-mcp-adapters --with langchain \
    python tests/downstream/smoke_langchain_mcp_adapters.py
```
