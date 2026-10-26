---
name: fastmcp-server
description: Build, extend, or review MCP servers with Python FastMCP, using documentation matched to the project's version.
---

# Authoring MCP servers with FastMCP

## Find the current API

Check the project's installed FastMCP version and dependency constraints.
Fetch [the documentation index](https://gofastmcp.com/llms.txt) and read the
relevant pages for that version. The index includes versioned documentation;
select `/v3/` pages for FastMCP 3 and unversioned pages for FastMCP 4. Read
individual pages through their `.md` URLs instead of loading `llms-full.txt`.
For APIs newer than the installed release, check that release's source or
signatures. Preserve the project's dependency constraints unless an upgrade is
part of the request. For a new project, check the current stable release before
choosing a dependency.

Use the standalone `fastmcp` package. Its API differs from
`mcp.server.fastmcp` in the Python MCP SDK.

## Author the server

Choose the MCP interface to suit what the server exposes: tools for callable
operations, resources for addressable content, and prompts for reusable message
templates. FastMCP derives tool schemas from Python annotations and descriptions;
use `Literal` or enums for finite choices and `Field` descriptions for parameter
semantics. SDK wrappers need to preserve signatures and docstrings, for example
with `functools.wraps`, or discovery loses that information. Inspect the schema
the client receives, especially after transforms or custom discovery rendering:
requiredness, enum values, and nested fields can disappear from a summary.

Let the intended client tasks guide tool boundaries, descriptions, and result
shape. Give clients enough context to use results and recover from errors.
The right granularity depends on the service; generated API tools, custom tools,
and compositions can all be appropriate.

For paginated or bounded results, expose whether the answer is complete and how
to continue. An upstream failure is not an empty result. Return useful recovery
information through tool errors rather than a successful-looking payload. Use
`ToolError`, `ResourceError`, or `PromptError` from `fastmcp.exceptions` for
intentional client-facing messages. With `mask_error_details=True`, ordinary
exceptions such as `ValueError` lose their messages; do not put sensitive backend
details in the explicit error types, whose messages remain visible.

When using tool search or code mode, make exact named operations discoverable
and explain the execution contract: what persists between calls and what becomes
the result. A short fetch/filter/return example can prevent repeated empty
outputs. Check the installed implementation before describing that behavior.

Look up FastMCP's relevant facilities as needed: context and dependencies,
lifespan, authentication, composition, and transports. Use the project's runtime
and hosting choices to determine configuration.

When clients need a repeatable workflow across the server's capabilities,
consider shipping a skill alongside those capabilities. Put task selection,
operation sequencing, and recovery guidance in the skill; keep tool descriptions
focused on their individual contracts. Read the version-matched Skills Provider
and Experimental Skills Extension docs from the index before implementing it.
`SkillsDirectoryProvider` exposes skill files as resources; the experimental
extension adds `skills/list` and `skills/get` discovery. Check the installed
release and target host's support before selecting either interface. Discovery
does not activate a skill: verify that the host reads and uses it in the intended
workflow. The experimental extension currently requires directly registered
providers and does not support mounted or transformed skill sources.

Verify the server through FastMCP's `Client`, which can connect directly to a
server instance for in-process testing. Check a representative operation, its
client-visible schema and result, and a meaningful failure. For composed or
transformed servers, verify the public names and URIs through the outer server,
including resource references returned by prompts. Namespace transforms change
component names and resource URIs, but do not rewrite URI text inside prompt
messages. When a prompt must work both standalone and mounted, direct the client
to discover the resource template instead of hardcoding the child's URI.
Exercise the chosen transport when connection or deployment behavior is part
of the change.

## Evaluate agent workflows

When the task includes evaluating how agents use the server, test a representative
workflow using the project's existing evaluation harness when available.
Protocol tests show that a tool works; an agent task shows whether the
interface is usable. Start from a known condition, give an agent a user-level request,
and check the outcome against independent API or database evidence. For writes,
check both the intended change and what should remain unchanged. A normal agent
exit or a plausible final answer is not proof of success.

Use the target client's actual capabilities; exposing resources, sampling, or
elicitation does not mean that client uses them. To measure the server alone,
give the agent only that server's capabilities supported by the client. To measure
the user's workflow, include the other
capabilities the client really has. Inspect traces for discovery misses, invalid
arguments, partial-data assumptions, and repeated recovery attempts before
changing tool descriptions or structure. Compare task success alongside calls,
latency, and context size; fewer tools or tokens alone is not the objective.
