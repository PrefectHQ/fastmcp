# FastMCP Development Guidelines

## Required Development Workflow

```bash
uv sync                              # Install dependencies
uv run pre-commit run --all-files    # Ruff + Prettier + Pyright
uv run pytest                        # Run full test suite
```

**Tests must pass and lint/typing must be clean before committing.**

## Repository Structure

| Path             | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `src/fastmcp/`   | Library source code (Python ≥ 3.10)                   |
| `  └─server/`    | Server implementation, `FastMCP`, auth, networking    |
| `  └─client/`    | High-level client SDK + helpers                       |
| `  └─resources/` | MCP resources and resource templates                  |
| `  └─prompts/`   | Prompt templates                                      |
| `  └─tools/`     | Tool implementations                                  |
| `tests/`         | Pytest test suite                                     |
| `docs/`          | Mintlify documentation (published to gofastmcp.com)   |
| `examples/`      | Minimal runnable demos                                |

## Core MCP Objects

When modifying MCP functionality, changes typically need to be applied across all object types:
- **Tools** (`src/tools/` + `ToolManager`)
- **Resources** (`src/resources/` + `ResourceManager`)
- **Resource Templates** (`src/resources/` + `ResourceManager`)
- **Prompts** (`src/prompts/` + `PromptManager`)

## Testing Best Practices

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

### Commit Messages and Agent Attribution
- **NEVER** include agent attribution in commit messages or PR titles/descriptions (no "🤖 Generated with [tool]", "with Claude", etc.)
- Agent attribution is ONLY allowed in Co-authored-by lines in commits
- Keep commit messages brief - ideally just headlines, not detailed messages
- Focus on what changed, not how or why

### Code Standards
- Python ≥ 3.10 with full type annotations
- Follow existing patterns and maintain consistency
- Use `# type: ignore[attr-defined]` in tests for MCP results instead of type assertions
- Each feature needs corresponding tests

### Documentation
- Uses Mintlify framework
- Files must be in docs.json to be included
- Never modify `docs/python-sdk/**` (auto-generated)
