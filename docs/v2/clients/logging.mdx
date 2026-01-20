---
title: Server Logging
sidebarTitle: Logging
description: Receive and handle log messages from MCP servers.
icon: receipt
---

import { VersionBadge } from '/snippets/version-badge.mdx'

<VersionBadge version="2.0.0" />

MCP servers can emit log messages to clients. The client can handle these logs through a log handler callback.

## Log Handler

Provide a `log_handler` function when creating the client. For robust logging, the log messages can be integrated with Python's standard `logging` module.

```python
import logging
from fastmcp import Client
from fastmcp.client.logging import LogMessage

# In a real app, you might configure this in your main entry point
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Get a logger for the module where the client is used
logger = logging.getLogger(__name__)

# This mapping is useful for converting MCP level strings to Python's levels
LOGGING_LEVEL_MAP = logging.getLevelNamesMapping()

async def log_handler(message: LogMessage):
    """
    Handles incoming logs from the MCP server and forwards them
    to the standard Python logging system.
    """
    msg = message.data.get('msg')
    extra = message.data.get('extra')

    # Convert the MCP log level to a Python log level
    level = LOGGING_LEVEL_MAP.get(message.level.upper(), logging.INFO)

    # Log the message using the standard logging library
    logger.log(level, msg, extra=extra)


client = Client(
    "my_mcp_server.py",
    log_handler=log_handler,
)
```

## Handling Structured Logs

The `message.data` attribute is a dictionary that contains the log payload from the server. This enables structured logging, allowing you to receive rich, contextual information.

The dictionary contains two keys:
- `msg`: The string log message.
- `extra`: A dictionary containing any extra data sent from the server.

This structure is preserved even when logs are forwarded through a FastMCP proxy, making it a powerful tool for debugging complex, multi-server applications.

### Handler Parameters

The `log_handler` is called every time a log message is received. It receives a `LogMessage` object:

<Card icon="code" title="Log Handler Parameters">
<ResponseField name="LogMessage" type="Log Message Object">
  <Expandable title="attributes">
    <ResponseField name="level" type='Literal["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]'>
      The log level
    </ResponseField>

    <ResponseField name="logger" type="str | None">
      The logger name (optional, may be None)
    </ResponseField>

    <ResponseField name="data" type="dict">
      The log payload, containing `msg` and `extra` keys.
    </ResponseField>
  </Expandable>
</ResponseField>
</Card>

```python
async def detailed_log_handler(message: LogMessage):
    msg = message.data.get('msg')
    extra = message.data.get('extra')

    if message.level == "error":
        print(f"ERROR: {msg} | Details: {extra}")
    elif message.level == "warning":
        print(f"WARNING: {msg} | Details: {extra}")
    else:
        print(f"{message.level.upper()}: {msg}")
```

## Default Log Handling

If you don't provide a custom `log_handler`, FastMCP's default handler routes server logs to the appropriate Python logging levels. The MCP levels are mapped as follows: `notice` → INFO; `alert` and `emergency` → CRITICAL. If the server includes a logger name, it is prefixed in the message, and any `extra` data is forwarded via the logging `extra` parameter.

```python
client = Client("my_mcp_server.py")

async with client:
    # Server logs are forwarded at their proper severity (DEBUG/INFO/WARNING/ERROR/CRITICAL)
    await client.call_tool("some_tool")
```
