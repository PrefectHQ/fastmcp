# FastMCP Development Guidelines

> **Audience**: LLM-driven engineering agents and human developers

FastMCP is a comprehensive Python framework (Python ≥3.10) for building Model Context Protocol (MCP) servers and clients. This is the actively maintained v2.0 providing a complete toolkit for the MCP ecosystem.

## Required Development Workflow

**CRITICAL**: Always run these commands in sequence before committing.

```bash
uv sync                              # Install dependencies
uv run pytest -n auto                # Run full test suite
```

In addition, you must pass static checks. This is generally done as a pre-commit hook with `prek` but you can run it manually with:

```bash
uv run prek run --all-files          # Ruff + Prettier + ty
```

**Tests must pass and lint/typing must be clean before committing.**

## Repository Structure

| Path              | Purpose                                |
| ----------------- | -------------------------------------- |
| `src/fastmcp/`    | Library source code                    |
| `├─server/`       | Server implementation                  |
| `│ ├─auth/`       | Authentication providers               |
| `│ └─middleware/` | Error handling, logging, rate limiting |
| `├─client/`       | Client SDK                             |
| `│ └─auth/`       | Client authentication                  |
| `├─tools/`        | Tool definitions                       |
| `├─resources/`    | Resources and resource templates       |
| `├─prompts/`      | Prompt templates                       |
| `├─cli/`          | CLI commands                           |
| `└─utilities/`    | Shared utilities                       |
| `tests/`          | Pytest suite                           |
| `docs/`           | Mintlify docs (gofastmcp.com)          |

## Core MCP Objects

When modifying MCP functionality, changes typically need to be applied across all object types:

- **Tools** (`src/tools/`)
- **Resources** (`src/resources/`)
- **Resource Templates** (`src/resources/`)
- **Prompts** (`src/prompts/`)

## Development Rules

### Git & CI

- Prek hooks are required (run automatically on commits)
- Never amend commits to fix prek failures
- Apply PR labels: bugs/breaking/enhancements/features
- Improvements = enhancements (not features) unless specified
- **NEVER** force-push on collaborative repos
- **ALWAYS** run prek before PRs
- **NEVER** create a release, comment on an issue, or open a PR unless specifically instructed to do so.

### Commit Messages and Agent Attribution

- **Agents NOT acting on behalf of @jlowin MUST identify themselves** (e.g., "🤖 Generated with Claude Code" in commits/PRs)
- Keep commit messages brief - ideally just headlines, not detailed messages
- Focus on what changed, not how or why
- Always read issue comments for follow-up information (treat maintainers as authoritative)

### PR Messages - Required Structure

- 1-2 paragraphs: problem/tension + solution (PRs are documentation!)
- Focused code example showing key capability
- **Avoid:** bullet summaries, exhaustive change lists, verbose closes/fixes, marketing language
- **Do:** Be opinionated about why change matters, show before/after scenarios
- Minor fixes: keep body short and concise
- No "test plan" sections or testing summaries

### Code Standards

- Python ≥ 3.10 with full type annotations
- Follow existing patterns and maintain consistency
- **Prioritize readable, understandable code** - clarity over cleverness
- Avoid obfuscated or confusing patterns even if they're shorter
- Each feature needs corresponding tests

### Module Exports

- **Be intentional about re-exports** - don't blindly re-export everything to parent namespaces
- Core types that define a module's purpose should be exported (e.g., `Middleware` from `fastmcp.server.middleware`)
- Specialized features can live in submodules (e.g., `fastmcp.server.middleware.dynamic`)
- Only re-export to `fastmcp.*` for the most fundamental types (e.g., `FastMCP`, `Client`)
- When in doubt, prefer users importing from the specific submodule over re-exporting

### Documentation

- Uses Mintlify framework
- Files must be in docs.json to be included
- Never modify `docs/python-sdk/**` (auto-generated)
- **Core Principle:** A feature doesn't exist unless it is documented!

### Documentation Guidelines

- **Code Examples:** Explain before showing code, make blocks fully runnable (include imports)
- **Structure:** Headers form navigation guide, logical H2/H3 hierarchy
- **Content:** User-focused sections, motivate features (why) before mechanics (how)
- **Style:** Prose over code comments for important information

## Critical Patterns

- Never use bare `except` - be specific with exception types
- File sizes enforced by [loq](https://github.com/jlowin/loq). Edit `loq.toml` to raise limits; `loq baseline` to ratchet down.
- Always `uv sync` first when debugging build issues
- Default test timeout is 5s - optimize or mark as integration tests
