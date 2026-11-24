# fastmcp-events

Events for FastMCP servers and clients: the `io.modelcontextprotocol/events` extension defined in [SEP-3415](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3415), with poll and push delivery.

Experimental: the API may change in any release without a deprecation period.

```bash
pip install "fastmcp[events]"
# with the Redis-backed log, through Docket
pip install "fastmcp[events]" "fastmcp-events[docket]"
```

```python
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp_events import EventsExtension

mcp = FastMCP("alerts")
events = EventsExtension()
mcp.add_extension(events)


@events.event("alert.fired")
class AlertFired(BaseModel):
    """An alert fired."""

    project: str


# later, in a tool or webhook handler:
# await events.emit(AlertFired(project="api"))
```

See the [Events docs](https://gofastmcp.com/servers/events).
