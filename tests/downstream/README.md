# downstream smoke

Checks that FastMCP still works the way its consumers use it. The
[workflow](../../.github/workflows/run-downstream-smoke.yml) builds FastMCP's
wheels once, installs them where each consumer runs, and drives the consumer
against servers started from the checkout.

| job | what it runs | FastMCP install |
|---|---|---|
| pydantic-ai test suite | pydantic-ai's own `tests/test_mcp.py` and sampling tests at a pinned tag | wheels overlaid on pydantic-ai's locked environment, as their FastMCP 4 job does |
| pydantic-ai smoke | `smoke_pydantic_ai.py` over stdio, `mcp.json` config, streamable HTTP and SSE | client-only `fastmcp-slim[client]`, as `pydantic-ai-slim[mcp]` installs it |
| langchain smoke | `smoke_langchain.py` over stdio, streamable HTTP and SSE | none: the adapters pin `mcp<2`, so they reach FastMCP over the wire |

HTTP and SSE servers require a bearer token. Each smoke prints one line per
check, writes a table to the job summary, and reports warnings raised in the
consumer's process.

Pushes and PRs test the versions pinned in the workflow. The nightly run, or a
manual run with `latest`, tests each consumer's latest release.

## running locally

```bash
uv sync
uv build --wheel --out-dir dist fastmcp_slim

uv run --isolated --no-project --no-config \
    --with 'pydantic-ai-slim[mcp]' \
    --with "fastmcp-slim[client] @ file://$PWD/$(ls dist/fastmcp_slim-*.whl)" \
    python tests/downstream/smoke_pydantic_ai.py

uv run --isolated --no-project --no-config \
    --with langchain-mcp-adapters --with langchain \
    python tests/downstream/smoke_langchain.py
```
