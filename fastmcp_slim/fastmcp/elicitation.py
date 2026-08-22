"""Declarative elicitation for FastMCP.

Annotate a parameter with `Elicit(...)` to have it filled by asking the client
rather than by the model:

```python
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.elicitation import Elicit

mcp = FastMCP("Booking Server")


@mcp.tool
async def book_flight(
    destination: Annotated[str, Elicit("Where would you like to fly?")],
) -> str:
    return f"Booked a flight to {destination}"
```

The parameter is hidden from the tool's input schema, and the same function
works on both protocol eras — the framework picks the transport.

This module is the stable import location. The implementation behind it is
expected to move into the `uncalled-for` dependency engine; importing `Elicit`
from here keeps that move invisible.
"""

from fastmcp.server._elicit_resolution import Elicit

__all__ = ["Elicit"]
