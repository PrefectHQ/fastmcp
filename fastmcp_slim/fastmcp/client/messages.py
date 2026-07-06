from typing import TypeAlias

import mcp_types
from mcp.client.session import MessageHandlerFnT
from mcp.shared.session import RequestResponder

Message: TypeAlias = (
    RequestResponder[mcp_types.ServerRequest, mcp_types.ClientResult]
    | mcp_types.ServerNotification
    | Exception
)

MessageHandlerT: TypeAlias = MessageHandlerFnT


class MessageHandler:
    """
    This class is used to handle MCP messages sent to the client. It is used to handle all messages,
    requests, notifications, and exceptions. Users can override any of the hooks
    """

    async def __call__(
        self,
        message: RequestResponder[mcp_types.ServerRequest, mcp_types.ClientResult]
        | mcp_types.ServerNotification
        | Exception,
    ) -> None:
        return await self.dispatch(message)

    async def dispatch(self, message: Message) -> None:
        # handle all messages
        await self.on_message(message)

        # SDK v2 delivers server-to-client requests wrapped in a
        # RequestResponder (with the request unwrapped on `.request`) and
        # notifications unwrapped (the monolith notification model itself, no
        # `.root` wrapper). `ServerNotification`/`ServerRequest` are UnionTypes,
        # so they can't appear in class match patterns — branch on the concrete
        # models directly.
        if isinstance(message, RequestResponder):
            # handle all requests
            # ty doesn't narrow the generic RequestResponder cleanly here.
            await self.on_request(message)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

            # handle specific requests
            request = message.request
            match request:
                case mcp_types.PingRequest():
                    await self.on_ping(request)
                case mcp_types.ListRootsRequest():
                    await self.on_list_roots(request)
                case mcp_types.CreateMessageRequest():
                    await self.on_create_message(request)

        elif isinstance(message, Exception):
            await self.on_exception(message)

        else:
            # notifications (unwrapped monolith models)
            await self.on_notification(message)

            # handle specific notifications
            match message:
                case mcp_types.CancelledNotification():
                    await self.on_cancelled(message)
                case mcp_types.ProgressNotification():
                    await self.on_progress(message)
                case mcp_types.LoggingMessageNotification():
                    await self.on_logging_message(message)
                case mcp_types.ToolListChangedNotification():
                    await self.on_tool_list_changed(message)
                case mcp_types.ResourceListChangedNotification():
                    await self.on_resource_list_changed(message)
                case mcp_types.PromptListChangedNotification():
                    await self.on_prompt_list_changed(message)
                case mcp_types.ResourceUpdatedNotification():
                    await self.on_resource_updated(message)

    async def on_message(self, message: Message) -> None:
        pass

    async def on_request(
        self, message: RequestResponder[mcp_types.ServerRequest, mcp_types.ClientResult]
    ) -> None:
        pass

    async def on_ping(self, message: mcp_types.PingRequest) -> None:
        pass

    async def on_list_roots(self, message: mcp_types.ListRootsRequest) -> None:
        pass

    async def on_create_message(self, message: mcp_types.CreateMessageRequest) -> None:
        pass

    async def on_notification(self, message: mcp_types.ServerNotification) -> None:
        pass

    async def on_exception(self, message: Exception) -> None:
        pass

    async def on_progress(self, message: mcp_types.ProgressNotification) -> None:
        pass

    async def on_logging_message(
        self, message: mcp_types.LoggingMessageNotification
    ) -> None:
        pass

    async def on_tool_list_changed(
        self, message: mcp_types.ToolListChangedNotification
    ) -> None:
        pass

    async def on_resource_list_changed(
        self, message: mcp_types.ResourceListChangedNotification
    ) -> None:
        pass

    async def on_prompt_list_changed(
        self, message: mcp_types.PromptListChangedNotification
    ) -> None:
        pass

    async def on_resource_updated(
        self, message: mcp_types.ResourceUpdatedNotification
    ) -> None:
        pass

    async def on_cancelled(self, message: mcp_types.CancelledNotification) -> None:
        pass
