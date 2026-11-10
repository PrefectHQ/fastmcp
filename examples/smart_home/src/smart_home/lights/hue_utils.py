"""One verified, pooled Hue connection for the server's lifetime."""

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from phue import Bridge

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.lifespan import lifespan
from smart_home.settings import Settings


@lifespan
async def hue_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Bridge]]:
    settings = Settings()
    verify: bool | ssl.SSLContext = True
    if settings.hue_bridge_certificate is not None:
        verify = ssl.create_default_context(cafile=str(settings.hue_bridge_certificate))
        verify.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        # The explicitly trusted bridge certificate identifies the bridge, not its IP.
        verify.check_hostname = False
    async with Bridge(
        settings.hue_bridge_ip, settings.hue_bridge_username, verify=verify
    ) as bridge:
        yield {"bridge": bridge}


@asynccontextmanager
async def get_bridge(ctx: Context = CurrentContext()) -> AsyncIterator[Bridge]:
    # Yield an already-open client; returning it would make DI enter it again.
    yield ctx.lifespan_context["bridge"]
