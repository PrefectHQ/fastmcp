---
title: event_store
sidebarTitle: event_store
---

# `fastmcp.server.event_store`


EventStore implementation backed by AsyncKeyValue.

This module provides an EventStore implementation that enables SSE polling/resumability
for Streamable HTTP transports. Events are stored using the key_value package's
AsyncKeyValue protocol, allowing users to configure any compatible backend
(in-memory, Redis, etc.) following the same pattern as ResponseCachingMiddleware.


## Classes

### `EventEntry` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/event_store.py#L26" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


Stored event entry.


### `StreamEventList` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/event_store.py#L34" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


List of event IDs for a stream.


### `EventStore` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/event_store.py#L40" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>


EventStore implementation backed by AsyncKeyValue.

Enables SSE polling/resumability by storing events that can be replayed
when clients reconnect. Works with any AsyncKeyValue backend (memory, Redis, etc.)
following the same pattern as ResponseCachingMiddleware and OAuthProxy.

**Args:**
- `storage`: AsyncKeyValue backend. Defaults to MemoryStore.
- `max_events_per_stream`: Maximum events to retain per stream. Default 100.
- `ttl`: Event TTL in seconds. Default 3600 (1 hour). Set to None for no expiration.


**Methods:**

#### `store_event` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/event_store.py#L94" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
store_event(self, stream_id: StreamId, message: JSONRPCMessage | None) -> EventId
```

Store an event and return its ID.

**Args:**
- `stream_id`: ID of the stream the event belongs to
- `message`: The JSON-RPC message to store, or None for priming events

**Returns:**
- The generated event ID for the stored event


#### `replay_events_after` <sup><a href="https://github.com/jlowin/fastmcp/blob/main/src/fastmcp/server/event_store.py#L135" target="_blank"><Icon icon="github" style="width: 14px; height: 14px;" /></a></sup>

```python
replay_events_after(self, last_event_id: EventId, send_callback: EventCallback) -> StreamId | None
```

Replay events that occurred after the specified event ID.

**Args:**
- `last_event_id`: The ID of the last event the client received
- `send_callback`: A callback function to send events to the client

**Returns:**
- The stream ID of the replayed events, or None if the event ID was not found

