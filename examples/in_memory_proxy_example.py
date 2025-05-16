"""
This example demonstrates how to set up and use an in-memory FastMCP proxy.

It illustrates the pattern:
1. Create an original FastMCP server with some tools.
2. Create a Client that connects to this original server (in-memory).
3. Create a proxy FastMCP server using FastMCP.from_client(), passing it the client from step 2.
4. Use another Client to connect to the proxy server (in-memory) and interact with the original server's tools through the proxy.
"""

import asyncio

from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client


class EchoService:
    """A simple service to demonstrate with"""

    def echo(self, message: str) -> str:
        return f"Original server echoes: {message}"


async def main():
    print("--- In-Memory FastMCP Proxy Example ---")
    print("This example will walk through setting up an in-memory proxy.")
    print("-----------------------------------------")

    # 1. Original Server Setup
    print(
        "\nStep 1: Setting up the Original Server (OriginalEchoServer) with an 'echo' tool..."
    )
    original_server = FastMCP("OriginalEchoServer")
    original_server.add_tool(EchoService().echo)
    print(f"   -> Original Server '{original_server.name}' created.")

    # 2. Client for Proxy
    print("\nStep 2: Creating a Client to connect to the Original Server...")
    print("          (This client will be used internally by the proxy server)")
    client_to_original = Client(original_server)
    print(f"   -> Client for proxy created, targeting '{original_server.name}'.")

    # 3. Proxy Server Creation
    print("\nStep 3: Creating the Proxy Server (InMemoryProxy)...")
    print(
        f"          (Using FastMCP.from_client, passing it the client from Step 2 that targets '{original_server.name}')"
    )
    proxy_server = FastMCP.from_client(client_to_original, name="InMemoryProxy")
    print(
        f"   -> Proxy Server '{proxy_server.name}' created, proxying '{original_server.name}'."
    )

    # 4. Interacting via Proxy
    print("\nStep 4: Using a new Client to connect to the Proxy Server and interact...")
    async with Client(proxy_server) as final_client:
        print(f"   -> Successfully connected to proxy '{proxy_server.name}'.")

        print("\n   Listing tools available via proxy...")
        tools = await final_client.list_tools()
        if tools:
            print("      Available Tools:")
            for tool in tools:
                print(
                    f"        - {tool.name} (Description: {tool.description or 'N/A'})"
                )
        else:
            print("      No tools found via proxy.")

        message_to_echo = "Hello, simplified proxied world!"
        print(f"\n   Calling 'echo' tool via proxy with message: '{message_to_echo}'")
        try:
            result = await final_client.call_tool("echo", {"message": message_to_echo})
            if result and isinstance(result[0], TextContent):
                print(f"      Result from proxied 'echo' call: '{result[0].text}'")
            else:
                print(
                    f"      Error: Unexpected result format from proxied 'echo' call: {result}"
                )
        except Exception as e:
            print(f"      Error calling 'echo' tool via proxy: {e}")

    print("\n-----------------------------------------")
    print("--- In-Memory Proxy Example Finished ---")


if __name__ == "__main__":
    asyncio.run(main())
