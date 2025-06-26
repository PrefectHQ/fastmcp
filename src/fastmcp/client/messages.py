from typing import TypeAlias

import mcp.types
from mcp.client.session import MessageHandlerFnT
from mcp.shared.session import RequestResponder

Message: TypeAlias = (
    RequestResponder[mcp.types.ServerRequest, mcp.types.ClientResult]
    | mcp.types.ServerNotification
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
        message: RequestResponder[mcp.types.ServerRequest, mcp.types.ClientResult]
        | mcp.types.ServerNotification
        | Exception,
    ) -> None:
        return await self.dispatch(message)

    async def dispatch(self, message: Message) -> None:
        # handle all messages
        await self.on_message(message)

        match message:
            # requests
            case RequestResponder():
                # handle all requests
                await self.on_request(message)

                # handle specific requests
                match message.request.root:
                    case mcp.types.PingRequest():
                        await self.on_ping(message.request.root)
                    case mcp.types.ListRootsRequest():
                        await self.on_list_roots(message.request.root)
                    case mcp.types.CreateMessageRequest():
                        await self.on_create_message(message.request.root)

            # notifications
            case mcp.types.ServerNotification():
                # handle all notifications
                await self.on_notification(message)

                # handle specific notifications
                match message.root:
                    case mcp.types.CancelledNotification():
                        await self.on_cancelled(message.root)
                    case mcp.types.ProgressNotification():
                        await self.on_progress(message.root)
                    case mcp.types.LoggingMessageNotification():
                        await self.on_logging_message(message.root)
                    case mcp.types.ToolListChangedNotification():
                        await self.on_tool_list_changed(message.root)
                    case mcp.types.ResourceListChangedNotification():
                        await self.on_resource_list_changed(message.root)
                    case mcp.types.PromptListChangedNotification():
                        await self.on_prompt_list_changed(message.root)
                    case mcp.types.ResourceUpdatedNotification():
                        await self.on_resource_updated(message.root)

            case Exception():
                await self.on_exception(message)

    async def on_message(self, message: Message) -> None:
        pass

    async def on_request(
        self, message: RequestResponder[mcp.types.ServerRequest, mcp.types.ClientResult]
    ) -> None:
        pass

    async def on_ping(self, message: mcp.types.PingRequest) -> None:
        pass

    async def on_list_roots(self, message: mcp.types.ListRootsRequest) -> None:
        pass

    async def on_create_message(self, message: mcp.types.CreateMessageRequest) -> None:
        pass

    async def on_notification(self, message: mcp.types.ServerNotification) -> None:
        pass

    async def on_exception(self, message: Exception) -> None:
        pass

    async def on_progress(self, message: mcp.types.ProgressNotification) -> None:
        pass

    async def on_logging_message(
        self, message: mcp.types.LoggingMessageNotification
    ) -> None:
        pass

    async def on_tool_list_changed(
        self, message: mcp.types.ToolListChangedNotification
    ) -> None:
        pass

    async def on_resource_list_changed(
        self, message: mcp.types.ResourceListChangedNotification
    ) -> None:
        pass

    async def on_prompt_list_changed(
        self, message: mcp.types.PromptListChangedNotification
    ) -> None:
        pass

    async def on_resource_updated(
        self, message: mcp.types.ResourceUpdatedNotification
    ) -> None:
        pass

    async def on_cancelled(self, message: mcp.types.CancelledNotification) -> None:
        pass
