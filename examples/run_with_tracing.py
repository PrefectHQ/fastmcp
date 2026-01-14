#!/usr/bin/env python
"""Run a FastMCP server with OpenTelemetry tracing enabled.

Usage:
    uv run examples/run_with_tracing.py examples/echo.py --transport sse --port 8001

All arguments after the script name are passed to `fastmcp run`.
Traces are exported via OTLP to localhost:4317.

To view traces, run otel-desktop-viewer in another terminal:
    otel-desktop-viewer
    # Trace UI at http://localhost:8000, OTLP receiver on :4317

Install otel-desktop-viewer:
    brew install nico-barbas/brew/otel-desktop-viewer
"""

import os
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # Configure OTEL SDK before importing fastmcp
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    service_name = os.environ.get(
        "OTEL_SERVICE_NAME",
        f"fastmcp-{os.path.basename(sys.argv[1]).replace('.py', '')}",
    )

    # Set up tracer provider with OTLP exporter
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    print(f"Tracing enabled → OTLP {endpoint}", flush=True)
    print(f"Service: {service_name}", flush=True)
    print("View traces: otel-desktop-viewer (http://localhost:8000)\n", flush=True)

    # Now run fastmcp CLI
    from fastmcp.cli.cli import app

    sys.argv = ["fastmcp", "run", *sys.argv[1:]]
    app()


if __name__ == "__main__":
    main()
