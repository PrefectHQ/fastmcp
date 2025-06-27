from fastmcp import FastMCP
from fastmcp.contrib.component_manager import set_up_component_manager
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair

key_pair = RSAKeyPair.generate()

auth = BearerAuthProvider(
    public_key=key_pair.public_key,
    issuer="https://dev.example.com",
    audience="my-dev-server",
    required_scopes=["mcp:read"],
)

# Build main server
mcp_token = key_pair.create_token(
    subject="dev-user",
    issuer="https://dev.example.com",
    audience="my-dev-server",
    scopes=["mcp:write", "mcp:read"],
)
mcp = FastMCP(
    name="Component Manager",
    instructions="This is a test server with component manager.",
    auth=auth,
)

# Set up main server component manager
set_up_component_manager(server=mcp, required_scopes=["mcp:write"])

# Build mounted server
mounted_token = key_pair.create_token(
    subject="dev-user",
    issuer="https://dev.example.com",
    audience="my-dev-server",
    scopes=["mounted:write", "mcp:read"],
)
mounted = FastMCP(
    name="Component Manager",
    instructions="This is a test server with component manager.",
    auth=auth,
)

# Set up mounted server component manager
set_up_component_manager(server=mounted, required_scopes=["mounted:write"])

# Mount
mcp.mount(server=mounted, prefix="mo")


@mcp.resource("resource://greeting")
def get_greeting() -> str:
    """Provides a simple greeting message."""
    return "Hello from FastMCP Resources!"


@mounted.tool("greeting")
def get_info() -> str:
    """Provides a simple info."""
    return "You are using component manager contrib module!"
