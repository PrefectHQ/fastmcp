#!/usr/bin/env python
"""Client script to exercise all diagnostics server components with tracing.

Usage:
    # First, start the diagnostics server with tracing in one terminal:
    uv run examples/run_with_tracing.py examples/diagnostics/server.py --transport sse --port 8001

    # Then run this client in another terminal:
    uv run examples/diagnostics/client_with_tracing.py

    # View traces in otel-desktop-viewer (http://localhost:8000):
    otel-desktop-viewer

This script exercises all 8 components:
- 4 successful: ping, diag://status, diag://echo/{message}, greet prompt
- 4 error: fail_tool, diag://error, diag://error/{code}, fail prompt
"""

from __future__ import annotations

import asyncio
import os

# Configure OTEL SDK before importing fastmcp
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing():
    """Set up OpenTelemetry tracing with OTLP export."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    service_name = os.environ.get("OTEL_SERVICE_NAME", "fastmcp-diagnostics-client")

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    print(f"Tracing enabled: OTLP {endpoint}")
    print(f"Service: {service_name}")
    print("View traces: otel-desktop-viewer (http://localhost:8000)\n")


async def main():
    setup_tracing()

    from fastmcp import Client

    server_url = os.environ.get("DIAGNOSTICS_SERVER_URL", "http://localhost:8001/sse")
    print(f"Connecting to: {server_url}\n")

    async with Client(server_url) as client:
        # List all available components
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()

        print(f"Found {len(tools)} tools: {[t.name for t in tools]}")
        print(f"Found {len(resources)} resources: {[r.uri for r in resources]}")
        print(f"Found {len(prompts)} prompts: {[p.name for p in prompts]}\n")

        # === SUCCESSFUL OPERATIONS ===
        print("=" * 60)
        print("SUCCESSFUL OPERATIONS")
        print("=" * 60)

        # Local successful components
        print("\n--- Local Tools ---")
        result = await client.call_tool("ping", {})
        print(f"ping: {result}")

        print("\n--- Local Resources ---")
        result = await client.read_resource("diag://status")
        print(f"diag://status: {result}")

        result = await client.read_resource("diag://echo/hello-world")
        print(f"diag://echo/hello-world: {result}")

        print("\n--- Local Prompts ---")
        result = await client.get_prompt("greet", {"name": "Diagnostics"})
        print(
            f"greet: {result.messages[0].content.text if result.messages else result}"
        )

        # Proxied components from echo server
        print("\n--- Proxied Tools ---")
        try:
            result = await client.call_tool(
                "proxied_echo_tool", {"text": "proxied test"}
            )
            print(f"proxied_echo_tool: {result}")
        except Exception as e:
            print(f"proxied_echo_tool: ERROR - {e}")

        print("\n--- Proxied Resources ---")
        try:
            # Resource: echo://static -> echo://proxied/static
            result = await client.read_resource("echo://proxied/static")
            print(f"echo://proxied/static: {result}")
        except Exception as e:
            print(f"echo://proxied/static: ERROR - {e}")

        try:
            # Template: echo://{text} -> echo://proxied/{text}
            result = await client.read_resource("echo://proxied/test-message")
            print(f"echo://proxied/test-message: {result}")
        except Exception as e:
            print(f"echo://proxied/test-message: ERROR - {e}")

        print("\n--- Proxied Prompts ---")
        try:
            result = await client.get_prompt("proxied_echo", {"text": "proxied prompt"})
            print(
                f"proxied_echo: {result.messages[0].content.text if result.messages else result}"
            )
        except Exception as e:
            print(f"proxied_echo: ERROR - {e}")

        # === ERROR OPERATIONS ===
        print("\n" + "=" * 60)
        print("ERROR OPERATIONS (expected to fail)")
        print("=" * 60)

        print("\n--- Error Tools ---")
        try:
            await client.call_tool("fail_tool", {})
            print("fail_tool: UNEXPECTED SUCCESS")
        except Exception as e:
            print(f"fail_tool: {type(e).__name__} - {e}")

        print("\n--- Error Resources ---")
        try:
            await client.read_resource("diag://error")
            print("diag://error: UNEXPECTED SUCCESS")
        except Exception as e:
            print(f"diag://error: {type(e).__name__} - {e}")

        try:
            await client.read_resource("diag://error/500")
            print("diag://error/500: UNEXPECTED SUCCESS")
        except Exception as e:
            print(f"diag://error/500: {type(e).__name__} - {e}")

        print("\n--- Error Prompts ---")
        try:
            await client.get_prompt("fail", {})
            print("fail: UNEXPECTED SUCCESS")
        except Exception as e:
            print(f"fail: {type(e).__name__} - {e}")

    print("\n" + "=" * 60)
    print("DONE - Check otel-desktop-viewer for traces")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
