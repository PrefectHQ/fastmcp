"""
Remote MCP Server Example — TWZRD Agent Intel
==============================================

This example shows how to connect to a **remote** MCP server using FastMCP's
Client API. TWZRD Agent Intel (https://intel.twzrd.xyz) is a live production
FastMCP server providing trust scoring for Web3 AI agents over streamable-http.

PyPI: pip install twzrd-agent-intel
Docs: https://intel.twzrd.xyz

The server exposes three tools:
  - score_agent(wallet)     → trust score 0-100 + risk signals (free)
  - preflight_check(wallet) → quick go/no-go before x402 payment (free)
  - get_trust_receipt(wallet) → signed on-chain receipt (x402 paid)

This example demonstrates:
  1. Connecting to a remote FastMCP server via URL (streamable-http)
  2. Listing tools from the remote server
  3. Calling a tool and handling the result
  4. Using FastMCP's proxy pattern to re-expose remote tools locally
"""
import asyncio

import fastmcp

# TWZRD Agent Intel MCP server — live production endpoint
TWZRD_MCP_URL = "https://intel.twzrd.xyz/mcp"

# Example Web3 agent wallet (known Dexter repeat payer with 48x paid calls)
EXAMPLE_WALLET = "D1QkbFJKiPsymJ65RKHhF6DFB8sPMfpBaFBzuHKfJGWi"


# ── Example 1: Direct tool call ──────────────────────────────────────────────

async def score_agent_direct(wallet: str) -> str:
    """Connect to TWZRD MCP and score an agent wallet directly."""
    async with fastmcp.Client(TWZRD_MCP_URL) as client:
        result = await client.call_tool("score_agent", {"wallet": wallet})
        return result[0].text


async def run_direct_example() -> None:
    print("=== Direct Tool Call ===")
    print(f"Scoring wallet: {EXAMPLE_WALLET}")
    score = await score_agent_direct(EXAMPLE_WALLET)
    print(f"Result: {score}")
    print()


# ── Example 2: List tools from remote server ─────────────────────────────────

async def list_remote_tools() -> None:
    print("=== Remote Tool Discovery ===")
    async with fastmcp.Client(TWZRD_MCP_URL) as client:
        tools = await client.list_tools()
        print(f"Found {len(tools)} tools on {TWZRD_MCP_URL}:")
        for tool in tools:
            print(f"  {tool.name}: {tool.description}")
    print()


# ── Example 3: Proxy pattern — re-expose remote tools locally ────────────────

def create_local_proxy() -> fastmcp.FastMCP:
    """
    Create a local FastMCP server that proxies TWZRD tools.

    This is useful when you want to add middleware, rate limiting,
    or local caching on top of a remote MCP server.
    """
    proxy = fastmcp.FastMCP.as_proxy(
        fastmcp.Client(TWZRD_MCP_URL),
        name="twzrd-local-proxy",
    )
    return proxy


async def run_all_examples() -> None:
    await list_remote_tools()
    await run_direct_example()

    print("=== Proxy Server ===")
    proxy = create_local_proxy()
    print(f"Proxy server '{proxy.name}' created.")
    print("Run with: proxy.run(transport='streamable-http', port=8001)")
    print("This re-exposes all TWZRD tools locally at http://localhost:8001/mcp")


if __name__ == "__main__":
    asyncio.run(run_all_examples())
