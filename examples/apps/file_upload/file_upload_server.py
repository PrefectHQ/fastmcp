"""File upload — bypass the LLM context window to get files onto the server.

Usage:
    uv run python file_upload_server.py
"""

from fastmcp import FastMCP
from fastmcp.apps.file_upload import FileUpload

mcp = FastMCP("File Upload Server", providers=[FileUpload()])

if __name__ == "__main__":
    mcp.run()
