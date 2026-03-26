# FastMCP Contribution Tasks

## Overview

Working on bug fixes for the FastMCP project (PrefectHQ/fastmcp).
Fork: https://github.com/syhstanley/fastmcp

---

## Tasks

| # | Issue | Title | Status |
|---|-------|-------|--------|
| 1 | [#3571](https://github.com/PrefectHQ/fastmcp/issues/3571) | Mounted background tasks bind CurrentFastMCP() and Context.fastmcp to parent instead of child | 🔄 In Progress |

---

## Environment Setup

- **Date**: 2026-03-26
- **uv**: 0.11.1 (installed via astral.sh)
- **prek**: installed (pre-commit hooks active)
- **Python**: managed by uv

### Setup Commands Run
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd /home/stanley/opensource/fastmcp
uv sync
uv run prek install
```

---

## References

- Contributing guide: https://gofastmcp.com/development/contributing
- CLAUDE.md / AGENTS.md: See repo root
- CONTRIBUTING.md: See repo root
