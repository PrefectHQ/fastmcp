# AGENTS

This repository contains the FastMCP source code and documentation.
It is organized as follows:

- `src/fastmcp/` – the main library implementation. Key modules include:
  - `server/` with the `FastMCP` server and supporting classes like `Context`.
  - `client/` with the high-level `Client` for connecting to MCP servers.
  - subpackages for `resources`, `prompts`, `tools`, and other utilities.
- `tests/` – pytest-based unit tests for the library.
- `docs/` – documentation written for Mintlify and published on gofastmcp.com.
- `examples/` – small example applications demonstrating library usage.

Before committing any changes, run:

```bash
uv sync           # install dependencies
uv run pre-commit run --all-files
uv run pytest
```

`pre-commit` runs Ruff, Prettier, and Pyright. Make sure changes under
`src/` or `tests/` include corresponding tests. Please keep this file
updated if the repository layout or tooling changes.
