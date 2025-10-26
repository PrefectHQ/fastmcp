# /// script
# dependencies = ["aiohttp", "fastmcp"]
# ///

# uv pip install aiohttp fastmcp

import aiohttp

from fastmcp.server import FastMCP
from fastmcp.utilities.types import File


def create_server():
    mcp = FastMCP(name="File Demo", instructions="Get files from the server or URL.")

    @mcp.tool()
    async def get_test_file_from_server(path: str = "requirements.txt") -> File:
        """
        Get a test file from the server. If the path is not provided, it defaults to 'requirements.txt'.
        """
        return File(path=path)

    @mcp.tool()
    async def get_test_pdf_from_url(
        url: str = "https://mozilla.github.io/pdf.js/web/compressed.tracemonkey-pldi-09.pdf",
    ) -> File:
        """
        Get a test PDF file from a URL. If the URL is not provided, it defaults to a sample PDF.
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                pdf_data = await response.read()
                return File(data=pdf_data, format="pdf")

    return mcp


if __name__ == "__main__":
    create_server().run(transport="sse", host="0.0.0.0", port=8001, path="/sse")
