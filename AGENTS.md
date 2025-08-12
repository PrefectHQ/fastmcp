# FastMCP Development Guidelines

> **Audience**: LLM-driven engineering agents and human developers

FastMCP is a comprehensive Python framework (Python ≥3.10) for building Model Context Protocol (MCP) servers and clients. This is the actively maintained v2.0 providing a complete toolkit for the MCP ecosystem.

## Required Development Workflow

**CRITICAL**: Always run these commands in sequence before committing:

```bash
uv sync                              # Install dependencies
uv run pre-commit run --all-files    # Ruff + Prettier + Pyright
uv run pytest                        # Run full test suite
```

**All three must pass** - this is enforced by CI. Alternative: `just build && just typecheck && just test`

**Tests must pass and lint/typing must be clean before committing.**

## Repository Structure

| Path             | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `src/fastmcp/`   | Library source code (Python ≥ 3.10)                   |
| `├─server/`    | Server implementation, `FastMCP`, auth, networking    |
| `│  ├─auth/`   | Authentication providers (Bearer, JWT, WorkOS)        |
| `│  └─middleware/` | Error handling, logging, rate limiting             |
| `├─client/`    | High-level client SDK + transports                    |
| `│  └─auth/`   | Client authentication (Bearer, OAuth)                 |
| `├─tools/`     | Tool implementations + `ToolManager`                  |
| `├─resources/` | Resources, templates + `ResourceManager`              |
| `├─prompts/`   | Prompt templates + `PromptManager`                     |
| `├─cli/`       | FastMCP CLI commands (`run`, `dev`, `install`)         |
| `├─contrib/`   | Community contributions (bulk caller, mixins)         |
| `├─experimental/` | Experimental features (new OpenAPI parser)         |
| `└─utilities/` | Shared utilities (logging, JSON schema, HTTP)         |
| `tests/`         | Comprehensive pytest suite with markers               |
| `docs/`          | Mintlify documentation (published to gofastmcp.com)   |
| `examples/`      | Runnable demo servers (echo, smart_home, atproto)     |

## Core MCP Objects

When modifying MCP functionality, changes typically need to be applied across all object types:

- **Tools** (`src/tools/` + `ToolManager`)
- **Resources** (`src/resources/` + `ResourceManager`)
- **Resource Templates** (`src/resources/` + `ResourceManager`)
- **Prompts** (`src/prompts/` + `PromptManager`)

## Testing Best Practices

### Testing Standards

- Every test: atomic, self-contained, single functionality
- Use parameterization for multiple examples of same functionality
- Use separate tests for different functionality pieces
- Put imports at the top of the file, not in the test body
- **NEVER** add `@pytest.mark.asyncio` to tests - `asyncio_mode = "auto"` is set globally
- **ALWAYS** run pytest after significant changes

### Always Use In-Memory Transport

Pass FastMCP servers directly to clients for testing:

```python
mcp = FastMCP("TestServer")

@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

# Direct connection - no network complexity
async with Client(mcp) as client:
    result = await client.call_tool("greet", {"name": "World"})
```

Only use HTTP transport when explicitly testing network features:

```python
# Network testing only
async with Client(transport=StreamableHttpTransport(server_url)) as client:
    result = await client.ping()
```

## Development Rules

### Git & CI

- Pre-commit hooks are required (run automatically on commits)
- Never amend commits to fix pre-commit failures
- Apply PR labels: bugs/breaking/enhancements/features
- Improvements = enhancements (not features) unless specified
- **NEVER** force-push on collaborative repos
- **ALWAYS** run pre-commit before PRs

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
- Use `# type: ignore[attr-defined]` in tests for MCP results instead of type assertions
- Each feature needs corresponding tests

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

## Key Tools & Commands

### Environment Setup

```bash
git clone <repo>
cd fastmcp
uv sync                    # Installs all deps including dev tools
```

### Validation Commands (Run Frequently)

- **Linting**: `uv run ruff check` (or with `--fix`)
- **Type Checking**: `uv run pyright`
- **All Checks**: `uv run pre-commit run --all-files`

### Testing

- **Standard**: `uv run pytest`
- **Integration**: `uv run pytest -m "integration"`
- **Excluding markers**: `uv run pytest -m "not integration and not client_process"`

### CLI Usage

- **Run server**: `uv run fastmcp run server.py`
- **Development**: `uv run fastmcp dev server.py` (with Inspector UI)
- **Help**: `uv run fastmcp --help`

## Critical Patterns

### Error Handling

- Never use bare `except` - be specific with exception types
- Use `# type: ignore[attr-defined]` in tests for MCP results

### Build Issues (Common Solutions)

1. **Dependencies**: Always `uv sync` first
2. **Pre-commit fails**: Run `uv run pre-commit run --all-files` to see failures
3. **Type errors**: Use `uv run pyright` directly, check `pyproject.toml` config
4. **Test timeouts**: Default 3s - optimize or mark as integration tests
