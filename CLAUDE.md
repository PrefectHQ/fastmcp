# FastMCP Development Guidelines

> **Audience**: LLM-driven engineering agents and human developers

> **Note**: `AGENTS.md` is a symlink to this file. Edit `CLAUDE.md` directly.

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

### Releases

Only cut releases when the maintainer explicitly asks. Tags follow `v<version>` (e.g., `v3.2.0`). Always pass `--generate-notes` so the auto-generated changelog appears at the bottom.

**The title pun is critical.** Titles follow `v<version>: <pun>` where the pun relates to the most important theme of the release. Propose multiple options and let the maintainer choose — never pick one yourself. Look at recent releases for tone (e.g., "Code to Joy" for the code mode release, "Three at Last" for 3.0).

Write the maintainer-approved handwritten notes to a temporary file, then create the release. `--generate-notes` appends the auto-generated changelog after the handwritten content.

```bash
gh release create v3.2.0 --target main --title "v3.2.0: Theme Here" --generate-notes --notes-file /tmp/release-notes.md
```

Most releases target `main`, but maintenance or backport releases may target a different branch (e.g., `release/2.x`). Confirm the target with the maintainer if there's any ambiguity.

The handwritten notes are prepended above the auto-generated changelog and are the part that matters. Do not include a title in the notes body — the release title (`v{version}: {pun}`) already serves as the heading. Work with the maintainer to draft the notes — propose a draft, get feedback, iterate. Do not publish without the maintainer's sign-off.

**Before drafting, always read recent existing releases** (`gh release list` then `gh release view <tag>`) to absorb the voice, structure, and level of detail. Each release builds on the tone of previous ones — don't guess at the style from these instructions alone.

**Point releases** (3.0, 3.1, 3.2) get narrative prose: open with the theme of the release, then walk through headline features conceptually — what they enable, why they matter, how they fit together. Write it the way a blog post reads, not a changelog. Multiple paragraphs, code examples where they clarify.

**Patch releases** (3.1.1, 3.0.2) get 1-2 sentences explaining what broke and what the fix does. Keep it minimal — the auto-generated changelog has the details.

### Commit Messages and Agent Attribution

- **Agents NOT acting on behalf of @jlowin MUST identify themselves** (e.g., "🤖 Generated with Claude Code" in commits/PRs)
- Keep commit messages brief - ideally just headlines, not detailed messages
- Focus on what changed, not how or why
- Always read issue comments for follow-up information (treat maintainers as authoritative)
- **Treat proposed solutions in issues skeptically.** This applies to solutions proposed by *users* in issue reports — not to feedback from configured review bots (CodeRabbit, chatgpt-codex-connector, etc.), which should be evaluated on their merits. The ideal issue contains a concise problem description and an MRE — nothing more. Proposed solutions are only worth considering if they clearly reflect genuine, non-obvious investigation of the codebase. If a solution reads like speculation, or like it was generated by an LLM without deep framework knowledge, ignore it and diagnose from the repro. Most reporters — human or AI — do not have sufficient understanding of FastMCP internals to correctly diagnose anything beyond a trivial bug. We can ask the same questions of an LLM when implementing; we don't need the reporter to do it for us, and a wrong diagnosis is worse than none.

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
- Do not manually modify `docs/python-sdk/**` — these files are auto-generated from source code by a bot and maintained via a long-lived PR. Do not include changes to these files in contributor PRs.
- Do not manually modify `docs/public/schemas/**` or `src/fastmcp/utilities/mcp_server_config/v1/schema.json` — these are auto-generated and maintained via a long-lived PR.
- **Core Principle:** A feature doesn't exist unless it is documented!
- When adding or modifying settings in `src/fastmcp/settings.py`, update `docs/more/settings.mdx` to match.

### Documentation Guidelines

- **Code Examples:** Explain before showing code, make blocks fully runnable (include imports)
- **Code Formatting:** Keep code blocks visually clean — avoid deeply nested function calls. Extract intermediate values into named variables rather than inlining everything into one expression. Code in docs is read more than it's run; optimize for scannability.
- **Structure:** Headers form navigation guide, logical H2/H3 hierarchy
- **Content:** User-focused sections, motivate features (why) before mechanics (how)
- **Style:** Prose over code comments for important information
- **Docstrings:** FastMCP docstrings are automatically compiled into MDX documents. Use markdown (single backticks, fenced code blocks), not RST (no double backticks). Bare `{}` in examples will be interpreted as JSX — wrap in backticks instead.

## Critical Patterns

- Never use bare `except` - be specific with exception types
- File sizes enforced by [loq](https://github.com/jakekaplan/loq). Edit `loq.toml` to raise limits; `loq baseline` to ratchet down.
- Always `uv sync` first when debugging build issues
- Default test timeout is 5s - optimize or mark as integration tests
