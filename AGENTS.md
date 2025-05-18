# AGENTS

> **Audience**: LLM-driven engineering agents

This file provides guidance for autonomous coding agents working inside the **FastMCP** repository.

---

## Repository map

| Path             | Purpose                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------- |
| `src/fastmcp/`   | Library source code (Python ≥ 3.10)                                                      |
| `  └─server/`    | Server implementation, `FastMCP`, auth, networking                                       |
| `  └─client/`    | High‑level client SDK + helpers                                                          |
| `  └─resources/` | MCP resources and resource templates                                                     |
| `  └─prompts/`   | Prompt templates                                                                         |
| `  └─tools/`     | Tool implementations                                                                     |
| `tests/`         | Pytest test‑suite                                                                        |
| `docs/`          | Mintlify‑flavoured Markdown, published to [https://gofastmcp.com](https://gofastmcp.com) |
| `examples/`      | Minimal runnable demos                                                                   |

---

## Mandatory dev workflow

```bash
uv sync                              # install dependencies
uv run pre-commit run --all-files    # Ruff + Prettier + Pyright
uv run pytest                        # run full test suite
```

*Tests must pass* and *lint/typing must be clean* before committing.

### Core MCP objects

There are four major MCP object types:

- Tools (`src/tools/`)
- Resources (`src/resources/`)
- Resource Templates (`src/resources/`)
- Prompts (`src/prompts`)

While these have slightly different semantics and implementations, in general changes that affect interactions with any one (like adding tags, importing, etc.) will need to be adopted, applied, and tested on all others. Be sure to look at not only the object definition but also the related `Manager` (e.g. `ToolManager`, `ResourceManager`, and `PromptManager`). Also note that while resources and resource templates are different objects, they both are handled by the `ResourceManager`.

---

## Code conventions

* **Language:** Python ≥ 3.10
* **Style:** Enforced through pre-commit hooks
* **Type-checking:** Fully typed codebase
* **Tests:** Each feature should have corresponding tests

---

## Development guidelines

1. **Set up** the environment:
   ```bash
   uv sync && uv run pre-commit run --all-files
   ```
2. **Run tests**: `uv run pytest` until they pass.
3. **Iterate**: if a command fails, read the output, fix the code, retry.
4. Make the smallest set of changes that achieve the desired outcome.
5. Always read code before modifying it blindly.
6. Follow established patterns and maintain consistency.
