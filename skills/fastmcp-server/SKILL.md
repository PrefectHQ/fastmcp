---
name: fastmcp-server
description: Build, extend, or review MCP servers with Python FastMCP, using documentation matched to the project's version.
---

# Authoring MCP servers with FastMCP

## Find the current API

Check the project's installed FastMCP version and dependency constraints.
Fetch [the documentation index](https://gofastmcp.com/llms.txt) and read the
relevant pages for that version. The index includes versioned documentation;
select the matching major for FastMCP 3 or 4. For APIs newer than the installed
release, check that release's source or signatures. For a new project, check
the current stable release before choosing a dependency.

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
information through tool errors rather than a successful-looking payload.

When using tool search or code mode, make exact named operations discoverable
and explain the execution contract: what persists between calls and what becomes
the result. A short fetch/filter/return example can prevent repeated empty
outputs. Check the installed implementation before describing that behavior.

Look up FastMCP's relevant facilities as needed: context and dependencies,
lifespan, authentication, composition, and transports. Use the project's runtime
and hosting choices to determine configuration.

Verify the server through FastMCP's `Client`, which can connect directly to a
server instance for in-process testing. Check a representative operation, its
client-visible schema and result, and a meaningful failure. Exercise the chosen
transport when connection or deployment behavior is part of the change.

## Evaluate the capability

Protocol tests show that a tool works; an agent task shows whether the interface
is usable. Start from a known condition, give an agent a user-level request,
and check the outcome against independent API or database evidence. For writes,
check both the intended change and what should remain unchanged. A normal agent
exit or a plausible final answer is not proof of success.

Use the target client's actual capabilities; exposing resources, sampling, or
elicitation does not mean that client uses them. To measure the server alone,
give the agent only its tools. To measure the user's workflow, include the other
capabilities the client really has. Inspect traces for discovery misses, invalid
arguments, partial-data assumptions, and repeated recovery attempts before
changing tool descriptions or structure. Compare task success alongside calls,
latency, and context size; fewer tools or tokens alone is not the objective.
