# FastMCP Development Guidelines

> **Audience**: LLM-driven engineering agents and human developers

FastMCP is a comprehensive Python framework (Python ≥3.10) for building Model Context Protocol (MCP) servers and clients. This is the actively maintained v2.0 providing a complete toolkit for the MCP ecosystem.

## Required Development Workflow

**CRITICAL**: Always run these commands in sequence before committing:

```bash
uv sync                              # Install dependencies
uv run prek run --all-files          # Ruff + Prettier + ty
uv run pytest                        # Run full test suite
```

**All three must pass** - this is enforced by CI. Alternative: `just build && just typecheck && just test`

**Tests must pass and lint/typing must be clean before committing.**

## Repository Structure

| Path               | Purpose                                                                             |
| ------------------ | ----------------------------------------------------------------------------------- |
| `src/fastmcp/`     | Library source code (Python ≥ 3.10)                                                 |
| `├─server/`        | Server implementation, `FastMCP`, auth, networking                                  |
| `│  ├─auth/`       | Authentication providers (Google, GitHub, Azure, AWS, WorkOS, Auth0, JWT, and more) |
| `│  └─middleware/` | Error handling, logging, rate limiting                                              |
| `├─client/`        | High-level client SDK + transports                                                  |
| `│  └─auth/`       | Client authentication (Bearer, OAuth)                                               |
| `├─tools/`         | Tool implementations + `ToolManager`                                                |
| `├─resources/`     | Resources, templates + `ResourceManager`                                            |
| `├─prompts/`       | Prompt templates + `PromptManager`                                                  |
| `├─cli/`           | FastMCP CLI commands (`run`, `dev`, `install`)                                      |
| `├─contrib/`       | Community contributions (bulk caller, mixins)                                       |
| `├─experimental/`  | Experimental features (sampling handlers)                                           |
| `└─utilities/`     | Shared utilities (logging, JSON schema, HTTP)                                       |
| `tests/`           | Comprehensive pytest suite with markers                                             |
| `docs/`            | Mintlify documentation (published to gofastmcp.com)                                 |
| `examples/`        | Runnable demo servers (echo, smart_home, atproto)                                   |

## Core MCP Objects

When modifying MCP functionality, changes typically need to be applied across all object types:

- **Tools** (`src/tools/` + `ToolManager`)
- **Resources** (`src/resources/` + `ResourceManager`)
- **Resource Templates** (`src/resources/` + `ResourceManager`)
- **Prompts** (`src/prompts/` + `PromptManager`)

## Writing Style

- Be brief and to the point. Do not regurgitate information that can easily be gleaned from the code, except to guide the reader to where the code is located.
- **NEVER** use "This isn't..." or "not just..." constructions. State what something IS directly. Avoid defensive writing patterns like:
  - "This isn't X, it's Y" or "Not just X, but Y" → Just say "This is Y"
  - "Not just about X" → State the actual purpose
  - "We're not doing X, we're doing Y" → Just explain what you're doing
  - Any variation of explaining what something isn't before what it is

## Testing Best Practices

### Testing Standards

- Every test: atomic, self-contained, single functionality
- Use parameterization for multiple examples of same functionality
- Use separate tests for different functionality pieces
- **ALWAYS** Put imports at the top of the file, not in the test body
- **NEVER** add `@pytest.mark.asyncio` to tests - `asyncio_mode = "auto"` is set globally
- **ALWAYS** run pytest after significant changes

### Inline Snapshots

FastMCP uses `inline-snapshot` for testing complex data structures. On first run with empty `snapshot()`, pytest will auto-populate the expected value when running `pytest --inline-snapshot=create`. To update snapshots after intentional changes, run `pytest --inline-snapshot=fix`. This is particularly useful for testing JSON schemas and API responses.

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

## Code Review Guidelines

### Philosophy

Code review is about maintaining a healthy codebase while helping contributors succeed. The burden of proof is on the PR to demonstrate it adds value in the intended way. Your job is to help it get there through actionable feedback.

**Critical**: A perfectly written PR that adds unwanted functionality must still be rejected. The code must advance the codebase in the intended direction, not just be well-written. When rejecting, provide clear guidance on how to align with project goals.

Be friendly and welcoming while maintaining high standards. Call out what works well - this reinforces good patterns. When code needs improvement, be specific about why and how to fix it. Remember that PRs serve as documentation for future developers.

### Focus On

- **Does this advance the codebase in the intended direction?** (Even perfect code for unwanted features should be rejected)
- **API design and naming clarity** - Identify confusing patterns (e.g., parameter values that contradict defaults) or non-idiomatic code (mutable defaults, etc.). Contributed code will need to be maintained indefinitely, and by someone other than the author (unless the author is a maintainer).
- **Suggest specific improvements**, not generic "add more tests" comments
- **Think about API ergonomics and learning curve** from a user perspective

### For Agent Reviewers

- **Read the full context**: Always examine related files, tests, and documentation before reviewing
- **Check against established patterns**: Look for consistency with existing codebase conventions
- **Verify functionality claims**: Don't just read code - understand what it actually does
- **Consider edge cases**: Think through error conditions and boundary scenarios

### Avoid

- Generic feedback without specifics
- Hypothetical problems unlikely to occur
- Nitpicking organizational choices without strong reason
- Summarizing what the PR already describes
- Star ratings or excessive emojis
- Bikeshedding style preferences when functionality is correct
- Requesting changes without suggesting solutions
- Focusing on personal coding style over project conventions

### Tone

- Acknowledge good decisions ("This API design is clean")
- Be direct but respectful
- Explain impact ("This will confuse users because...")
- Remember: Someone else maintains this code forever

### Decision Framework

Before approving, ask yourself:

1. Does this PR achieve its stated purpose?
2. Is that purpose aligned with where the codebase should go?
3. Would I be comfortable maintaining this code?
4. Have I actually understood what it does, not just what it claims?
5. Does this change introduce technical debt?

If something needs work, your review should help it get there through specific, actionable feedback. If it's solving the wrong problem, say so clearly.

### Review Comment Examples

**Good Review Comments:**

❌ "Add more tests"  
✅ "The `handle_timeout` method needs tests for the edge case where timeout=0"

❌ "This API is confusing"  
✅ "The parameter name `data` is ambiguous - consider `message_content` to match the MCP specification"

❌ "This could be better"  
✅ "This approach works but creates a circular dependency. Consider moving the validation to `utils/validators.py`"

### Review Checklist

Before approving, verify:

- [ ] All required development workflow steps completed (uv sync, prek, pytest)
- [ ] Changes align with repository patterns and conventions
- [ ] API changes are documented and backwards-compatible where possible
- [ ] Error handling follows project patterns (specific exception types)
- [ ] Tests cover new functionality and edge cases

## Key Tools & Commands

### Environment Setup

```bash
git clone <repo>
cd fastmcp
uv sync                    # Installs all deps including dev tools
```

### Validation Commands (Run Frequently)

- **Linting**: `uv run ruff check` (or with `--fix`)
- **Type Checking**: `uv run ty check`
- **All Checks**: `uv run prek run --all-files`

### Testing

- **Standard**: `uv run pytest`
- **Integration**: `uv run pytest -m "integration"`
- **Excluding markers**: `uv run pytest -m "not integration and not client_process"`

### CLI Usage

- **Run server**: `uv run fastmcp run server.py`
- **Inspect server**: `uv run fastmcp inspect server.py`

## Critical Patterns

### Error Handling

- Never use bare `except` - be specific with exception types
- Use `# type: ignore[attr-defined]` in tests for MCP results

### Build Issues (Common Solutions)

1. **Dependencies**: Always `uv sync` first
2. **Prek fails**: Run `uv run prek run --all-files` to see failures
3. **Type errors**: Use `uv run ty check` directly, check `pyproject.toml` config
4. **Test timeouts**: Default 5s - optimize or mark as integration tests
