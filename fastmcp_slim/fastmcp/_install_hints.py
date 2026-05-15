CLIENT_SUPPORT = (
    "FastMCP client support is not installed. Install `fastmcp` or "
    "`fastmcp-slim[client]`."
)

SERVER_SUPPORT = (
    "FastMCP server support is not installed. Install `fastmcp` or "
    "`fastmcp-slim[server]`."
)

APP_SUPPORT = (
    "FastMCP app support is not installed. Install `fastmcp[apps]` or "
    "`fastmcp-slim[server,apps]`."
)

CLI_SUPPORT = (
    "FastMCP CLI support is not installed. Install `fastmcp` or `fastmcp-slim[server]`."
)


def full_package(feature: str) -> str:
    return (
        f"{feature} require the full `fastmcp` package. "
        "Install it with `pip install fastmcp`."
    )
