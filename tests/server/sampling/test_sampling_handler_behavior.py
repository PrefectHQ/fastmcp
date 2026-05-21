"""Tests for server sampling handler behavior modes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_REQUEST, ErrorData

from fastmcp.server.sampling.run import sample_step_impl


class DummySession:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.create_message_calls = 0

    def check_client_capability(self, *, capability):  # noqa: ANN001
        return True

    async def create_message(self, **kwargs):  # noqa: ANN003
        self.create_message_calls += 1
        if self.error is not None:
            raise self.error
        return "client response"


async def test_fallback_on_error_uses_handler_after_client_sampling_error():
    calls = []

    def sampling_handler(messages, params, request_context):  # noqa: ANN001, ANN202
        calls.append((messages, params, request_context))
        return "handler response"

    session = DummySession(
        error=McpError(
            ErrorData(
                code=INVALID_REQUEST,
                message="The user has denied permission to call this method.",
            )
        )
    )
    context = SimpleNamespace(
        fastmcp=SimpleNamespace(
            sampling_handler=sampling_handler,
            sampling_handler_behavior="fallback_on_error",
        ),
        session=session,
        request_context=object(),
        origin_request_id=None,
    )

    result = await sample_step_impl(context, "hello")

    assert session.create_message_calls == 1
    assert len(calls) == 1
    assert result.text == "handler response"


async def test_fallback_mode_preserves_client_sampling_errors():
    error = McpError(
        ErrorData(
            code=INVALID_REQUEST,
            message="The user has denied permission to call this method.",
        )
    )
    context = SimpleNamespace(
        fastmcp=SimpleNamespace(
            sampling_handler=lambda *args: "handler response",
            sampling_handler_behavior="fallback",
        ),
        session=DummySession(error=error),
        request_context=object(),
        origin_request_id=None,
    )

    with pytest.raises(McpError):
        await sample_step_impl(context, "hello")
