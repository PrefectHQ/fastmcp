import pytest
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider

from fastmcp.client.telemetry import client_span
from fastmcp.server.telemetry import delegate_span, seam_span, server_span


class OnStartRecorder(SpanProcessor):
    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, object]] = {}

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self.attributes[span.name] = dict(span.attributes or {})


def test_known_span_attributes_are_available_on_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = OnStartRecorder()
    provider = TracerProvider()
    provider.add_span_processor(recorder)
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("fastmcp.client.telemetry.get_tracer", lambda: tracer)
    monkeypatch.setattr("fastmcp.server.telemetry.get_tracer", lambda: tracer)

    with client_span(
        "client test",
        method="tools/call",
        component_key="tool:echo@",
        tool_name="echo",
    ):
        pass
    with server_span(
        "server test",
        method="tools/call",
        server_name="test-server",
        component_type="tool",
        component_key="tool:echo@",
        tool_name="echo",
    ):
        pass
    with seam_span("initialize test", server_name="test-server"):
        pass
    with delegate_span(
        "delegate test",
        provider_type="LocalProvider",
        component_key="tool:echo@",
        method="tools/call",
    ):
        pass

    assert recorder.attributes["client test"] == {
        "mcp.method.name": "tools/call",
        "fastmcp.component.key": "tool:echo@",
        "gen_ai.tool.name": "echo",
    }
    assert recorder.attributes["server test"] == {
        "mcp.method.name": "tools/call",
        "fastmcp.server.name": "test-server",
        "fastmcp.component.type": "tool",
        "fastmcp.component.key": "tool:echo@",
        "gen_ai.tool.name": "echo",
    }
    assert recorder.attributes["initialize test"] == {
        "fastmcp.span.seam": True,
        "mcp.method.name": "initialize test",
        "fastmcp.server.name": "test-server",
    }
    assert recorder.attributes["delegate delegate test"] == {
        "fastmcp.provider.type": "LocalProvider",
        "fastmcp.component.key": "tool:echo@",
        "mcp.method.name": "tools/call",
    }
