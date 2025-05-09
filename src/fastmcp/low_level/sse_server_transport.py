import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.sse import SseServerTransport as LowLevelSSEServerTransport
from mcp.shared.message import SessionMessage
from sse_starlette import EventSourceResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


class SseServerTransport(LowLevelSSEServerTransport):
    """
    Patched SSE server transport
    """

    @asynccontextmanager
    async def connect_sse(self, scope: Scope, receive: Receive, send: Send):
        """
        See https://github.com/modelcontextprotocol/python-sdk/pull/659/
        """
        if scope["type"] != "http":
            logger.error("connect_sse received non-HTTP request")
            raise ValueError("connect_sse can only handle HTTP requests")

        logger.debug("Setting up SSE connection")
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
        read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]

        write_stream: MemoryObjectSendStream[SessionMessage]
        write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        self._read_stream_writers[session_id] = read_stream_writer
        logger.debug(f"Created new session with ID: {session_id}")

        # Determine the full path for the message endpoint to be sent to the client.
        # scope['root_path'] is the prefix where the current Starlette app
        # instance is mounted.
        # e.g., "" if top-level, or "/api_prefix" if mounted under "/api_prefix".
        root_path = scope.get("root_path", "")

        # self._endpoint is the path *within* this app, e.g., "/messages".
        # Concatenating them gives the full absolute path from the server root.
        # e.g., "" + "/messages" -> "/messages"
        # e.g., "/api_prefix" + "/messages" -> "/api_prefix/messages"
        full_message_path_for_client = root_path.rstrip("/") + self._endpoint

        # This is the URI (path + query) the client will use to POST messages.
        client_post_uri_data = (
            f"{quote(full_message_path_for_client)}?session_id={session_id.hex}"
        )

        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream[
            dict[str, Any]
        ](0)

        async def sse_writer():
            logger.debug("Starting SSE writer")
            async with sse_stream_writer, write_stream_reader:
                await sse_stream_writer.send(
                    {"event": "endpoint", "data": client_post_uri_data}
                )
                logger.debug(f"Sent endpoint event: {client_post_uri_data}")

                async for session_message in write_stream_reader:
                    logger.debug(f"Sending message via SSE: {session_message}")
                    await sse_stream_writer.send(
                        {
                            "event": "message",
                            "data": session_message.message.model_dump_json(
                                by_alias=True, exclude_none=True
                            ),
                        }
                    )

        async with anyio.create_task_group() as tg:

            async def response_wrapper(scope: Scope, receive: Receive, send: Send):
                """
                The EventSourceResponse returning signals a client close / disconnect.
                In this case we close our side of the streams to signal the client that
                the connection has been closed.
                """
                await EventSourceResponse(
                    content=sse_stream_reader, data_sender_callable=sse_writer
                )(scope, receive, send)
                await read_stream_writer.aclose()
                await write_stream_reader.aclose()
                logging.debug(f"Client session disconnected {session_id}")

            logger.debug("Starting SSE response task")
            tg.start_soon(response_wrapper, scope, receive, send)

            logger.debug("Yielding read and write streams")
            yield (read_stream, write_stream)
