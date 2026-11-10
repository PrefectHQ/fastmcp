from typing import TypeAlias

import mcp_types
from mcp.client.session import MessageHandlerFnT

Message: TypeAlias = mcp_types.ServerNotification | Exception

MessageHandlerT: TypeAlias = MessageHandlerFnT


class MessageHandler:
    """
    This class is used to handle MCP messages sent to the client: notifications
    and transport-level exceptions. Users can override any of the hooks.

    Server-initiated *requests* (ping, sampling, roots) never reach this
    handler: the stable MCP SDK v2's `message_handler` contract only delivers
    `ServerNotification | Exception`, so a request has no wire path here.
    Those are answered through the `Client`'s dedicated callbacks instead —
    `sampling_handler=`, `roots=`, and `elicitation_handler=`.
    """

    async def __call__(self, message: mcp_types.ServerNotification | Exception) -> None:
        return await self.dispatch(message)

    async def dispatch(self, message: Message) -> None:
        # handle all messages
        await self.on_message(message)

        if isinstance(message, Exception):
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
