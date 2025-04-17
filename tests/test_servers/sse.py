import asyncio

import fastmcp_server

if __name__ == "__main__":
    asyncio.run(fastmcp_server.server.run_sse_async())
