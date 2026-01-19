# Resource Subscriptions

## Problem

The MCP spec supports resource subscriptions where:
1. Client subscribes to a resource URI
2. Server notifies client when that resource changes
3. Client can then re-read the resource

This enables real-time updates without polling. FastMCP doesn't currently implement this.

## Solution

Full implementation of MCP resource subscription protocol.

## API

### Server-side: Declaring Subscribable Resources

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("server")

# Option 1: Decorator parameter
@mcp.resource("data://metrics", subscribe=True)
async def get_metrics() -> dict:
    return {"cpu": 45.2, "memory": 60.1}

# Option 2: ResourceConfig
from fastmcp.resources import ResourceConfig

@mcp.resource(
    "data://metrics",
    config=ResourceConfig(subscribable=True)
)
async def get_metrics() -> dict:
    return {"cpu": 45.2, "memory": 60.1}
```

### Server-side: Notifying Subscribers

```python
@mcp.tool
async def update_metrics(ctx: Context, cpu: float, memory: float) -> str:
    global current_metrics
    current_metrics = {"cpu": cpu, "memory": memory}

    # Notify all subscribers that this resource changed
    await ctx.notify_resource_changed("data://metrics")

    return "Metrics updated"

# Or notify from anywhere with access to server
async def background_updater(server: FastMCP):
    while True:
        await asyncio.sleep(5)
        # Update data...
        await server.notify_resource_changed("data://metrics")
```

### Client-side: Subscribing

```python
from fastmcp import Client

async with Client("http://server:8000/mcp") as client:
    # Subscribe to a resource
    await client.subscribe_resource("data://metrics")

    # Handle notifications
    async for notification in client.resource_notifications():
        print(f"Resource changed: {notification.uri}")

        # Re-read the resource
        updated_data = await client.read_resource(notification.uri)
        print(f"New data: {updated_data}")
```

### Client-side: Unsubscribing

```python
async with Client("http://server:8000/mcp") as client:
    # Subscribe
    await client.subscribe_resource("data://metrics")

    # Later, unsubscribe
    await client.unsubscribe_resource("data://metrics")
```

### Client-side: List Subscriptions

```python
async with Client("http://server:8000/mcp") as client:
    await client.subscribe_resource("data://metrics")
    await client.subscribe_resource("data://logs")

    # Get all active subscriptions
    subs = client.list_subscriptions()
    assert "data://metrics" in subs
    assert "data://logs" in subs
```

## MCP Protocol

### Subscribe Request

```json
{
  "method": "resources/subscribe",
  "params": {
    "uri": "data://metrics"
  }
}
```

### Subscribe Response

```json
{
  "result": {}
}
```

### Unsubscribe Request

```json
{
  "method": "resources/unsubscribe",
  "params": {
    "uri": "data://metrics"
  }
}
```

### Resource Changed Notification

Server → Client notification:

```json
{
  "method": "notifications/resources/updated",
  "params": {
    "uri": "data://metrics"
  }
}
```

## Implementation

### Location

- `src/fastmcp/server/subscriptions.py` - Server-side subscription tracking
- `src/fastmcp/server/context.py` - Add `notify_resource_changed()` method
- `src/fastmcp/server/server.py` - Wire up subscription handlers
- `src/fastmcp/client/client.py` - Client subscription API
- `src/fastmcp/resources/resource.py` - Add `subscribe` parameter

### Server-side: Subscription Manager

```python
from collections import defaultdict
from weakref import WeakSet

class SubscriptionManager:
    """Tracks resource subscriptions per session."""

    def __init__(self):
        # uri -> set of session IDs
        self._subscriptions: dict[str, set[str]] = defaultdict(set)

        # session_id -> set of URIs (for cleanup)
        self._session_subscriptions: dict[str, set[str]] = defaultdict(set)

    def subscribe(self, uri: str, session_id: str) -> None:
        """Subscribe a session to a resource URI."""
        self._subscriptions[uri].add(session_id)
        self._session_subscriptions[session_id].add(uri)

    def unsubscribe(self, uri: str, session_id: str) -> None:
        """Unsubscribe a session from a resource URI."""
        self._subscriptions[uri].discard(session_id)
        self._session_subscriptions[session_id].discard(uri)

    def get_subscribers(self, uri: str) -> set[str]:
        """Get all session IDs subscribed to a URI."""
        return self._subscriptions.get(uri, set()).copy()

    def cleanup_session(self, session_id: str) -> None:
        """Remove all subscriptions for a session."""
        for uri in self._session_subscriptions.get(session_id, set()):
            self._subscriptions[uri].discard(session_id)
        del self._session_subscriptions[session_id]

    def list_subscriptions(self, session_id: str) -> list[str]:
        """List all URIs a session is subscribed to."""
        return list(self._session_subscriptions.get(session_id, set()))
```

### Server-side: Context Method

```python
class Context:
    async def notify_resource_changed(self, uri: str) -> None:
        """Notify all subscribers that a resource has changed."""
        manager = self._server._subscription_manager
        session_ids = manager.get_subscribers(uri)

        for session_id in session_ids:
            session = self._server._get_session(session_id)
            if session:
                await session.send_notification(
                    "notifications/resources/updated",
                    {"uri": uri}
                )
```

### Server-side: Session Notification

```python
class Session:
    async def send_notification(self, method: str, params: dict) -> None:
        """Send a notification to the client."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        if self.transport == "stdio":
            await self._stdio_write(message)
        elif self.transport in ("sse", "streamable-http"):
            await self._http_write_notification(message)
```

### Server-side: Handlers

In `server.py`:

```python
async def handle_subscribe(self, uri: str, ctx: Context) -> dict:
    """Handle resources/subscribe request."""
    # Check if resource exists and is subscribable
    resource = await self.get_resource(uri)
    if not resource:
        raise McpError(f"Resource not found: {uri}", code=-32602)

    if not resource.subscribable:
        raise McpError(f"Resource not subscribable: {uri}", code=-32602)

    # Add subscription
    self._subscription_manager.subscribe(uri, ctx.session_id)

    return {}

async def handle_unsubscribe(self, uri: str, ctx: Context) -> dict:
    """Handle resources/unsubscribe request."""
    self._subscription_manager.unsubscribe(uri, ctx.session_id)
    return {}
```

### Client-side: Subscription API

```python
class Client:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._subscriptions: set[str] = set()
        self._notification_queue: asyncio.Queue = asyncio.Queue()

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to resource change notifications."""
        result = await self.request(
            "resources/subscribe",
            {"uri": uri}
        )
        self._subscriptions.add(uri)

    async def unsubscribe_resource(self, uri: str) -> None:
        """Unsubscribe from resource change notifications."""
        await self.request(
            "resources/unsubscribe",
            {"uri": uri}
        )
        self._subscriptions.discard(uri)

    def list_subscriptions(self) -> list[str]:
        """List all active subscriptions."""
        return list(self._subscriptions)

    async def resource_notifications(self) -> AsyncIterator[ResourceNotification]:
        """Iterate over resource change notifications."""
        while True:
            notification = await self._notification_queue.get()
            if notification.method == "notifications/resources/updated":
                yield ResourceNotification(uri=notification.params["uri"])

    async def _handle_notification(self, notification: dict) -> None:
        """Handle incoming notifications from server."""
        await self._notification_queue.put(notification)
```

### Resource Configuration

In `resources/resource.py`:

```python
@dataclass
class ResourceConfig:
    subscribable: bool = False
    # ... other config

class Resource:
    def __init__(
        self,
        uri: str,
        *,
        subscribe: bool = False,
        config: ResourceConfig | None = None,
        **kwargs
    ):
        if config is None:
            config = ResourceConfig()

        if subscribe:
            config.subscribable = True

        self.subscribable = config.subscribable
```

## Edge Cases

1. **Session cleanup** - When a session ends, automatically unsubscribe all its subscriptions.

2. **Non-subscribable resources** - Return error if client tries to subscribe to a resource that doesn't support it.

3. **Resource doesn't exist** - Return error if subscribing to non-existent resource.

4. **Duplicate subscriptions** - Allow same session to subscribe multiple times (idempotent).

5. **stdio transport** - Notifications work over stdio using JSON-RPC 2.0 notification format.

6. **HTTP transport** - For SSE, notifications are sent as events. For streamable HTTP, they're sent in the response stream.

7. **Notification delivery failure** - If session is gone when notification fires, silently skip (already cleaned up).

8. **Resource URI patterns** - Template URIs can be subscribable. Notify on exact URI match only.

## Testing

Add `tests/server/test_subscriptions.py`:

```python
async def test_subscribe_resource():
    mcp = FastMCP("test")

    @mcp.resource("data://metrics", subscribe=True)
    def metrics():
        return {"value": 42}

    async with Client(mcp) as client:
        await client.subscribe_resource("data://metrics")
        assert "data://metrics" in client.list_subscriptions()

async def test_notification_delivery():
    mcp = FastMCP("test")

    @mcp.resource("data://metrics", subscribe=True)
    def metrics():
        return {"value": current_value}

    @mcp.tool
    async def update(ctx: Context, value: int):
        global current_value
        current_value = value
        await ctx.notify_resource_changed("data://metrics")

    async with Client(mcp) as client:
        await client.subscribe_resource("data://metrics")

        # Trigger notification
        await client.call_tool("update", {"value": 100})

        # Receive notification
        notification = await asyncio.wait_for(
            client.resource_notifications().__anext__(),
            timeout=1.0
        )

        assert notification.uri == "data://metrics"

async def test_non_subscribable_resource():
    mcp = FastMCP("test")

    @mcp.resource("data://config")  # No subscribe=True
    def config():
        return {"key": "value"}

    async with Client(mcp) as client:
        with pytest.raises(McpError, match="not subscribable"):
            await client.subscribe_resource("data://config")

async def test_session_cleanup():
    mcp = FastMCP("test")

    @mcp.resource("data://metrics", subscribe=True)
    def metrics():
        return {}

    async with Client(mcp) as client1:
        await client1.subscribe_resource("data://metrics")

    # Session ended, subscriptions should be cleaned up
    assert len(mcp._subscription_manager._subscriptions["data://metrics"]) == 0
```

## Documentation

Add to `docs/servers/resources.mdx`:

- Resource subscriptions overview
- Declaring subscribable resources
- Notifying subscribers
- Client subscription API
- Real-time updates pattern
- Comparison with polling

Add example in `docs/examples/`:

```python
# examples/subscriptions/server.py
"""Real-time metrics server with subscriptions."""

import asyncio
from fastmcp import FastMCP, Context

mcp = FastMCP("Metrics Server")

current_metrics = {"cpu": 0.0, "memory": 0.0}

@mcp.resource("data://metrics", subscribe=True)
def get_metrics() -> dict:
    return current_metrics

async def update_metrics_loop(server: FastMCP):
    """Background task that updates metrics every 5 seconds."""
    while True:
        await asyncio.sleep(5)

        # Simulate metrics update
        current_metrics["cpu"] = random.uniform(0, 100)
        current_metrics["memory"] = random.uniform(0, 100)

        # Notify all subscribers
        await server.notify_resource_changed("data://metrics")

@mcp.lifespan
async def lifespan(server):
    task = asyncio.create_task(update_metrics_loop(server))
    yield
    task.cancel()
```

```python
# examples/subscriptions/client.py
"""Client that subscribes to real-time metrics."""

import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:8000/mcp") as client:
        # Subscribe
        await client.subscribe_resource("data://metrics")
        print("Subscribed to metrics")

        # Listen for updates
        async for notification in client.resource_notifications():
            data = await client.read_resource(notification.uri)
            print(f"Metrics updated: {data}")

if __name__ == "__main__":
    asyncio.run(main())
```
