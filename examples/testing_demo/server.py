"""
FastMCP Testing Demo Server

A simple MCP server demonstrating tools, resources, and prompts
with comprehensive test coverage.
"""

from fastmcp import FastMCP

# Create server
mcp = FastMCP("Testing Demo")


# Tools
@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b


@mcp.tool
def greet(name: str, greeting: str = "Hello") -> str:
    """Greet someone with a customizable greeting"""
    return f"{greeting}, {name}!"


@mcp.tool
async def async_multiply(x: float, y: float) -> float:
    """Multiply two numbers (async example)"""
    return x * y


# Resources
@mcp.resource("demo://info")
def server_info() -> str:
    """Get server information"""
    return "This is the FastMCP Testing Demo server"


@mcp.resource("demo://greeting/{name}")
def greeting_resource(name: str) -> str:
    """Get a personalized greeting resource"""
    return f"Welcome to FastMCP, {name}!"


# Prompts
@mcp.prompt("hello")
def hello_prompt(name: str = "World") -> str:
    """Generate a hello world prompt"""
    return f"Say hello to {name} in a friendly way."


@mcp.prompt("explain")
def explain_prompt(topic: str, detail_level: str = "medium") -> str:
    """Generate a prompt to explain a topic"""
    if detail_level == "simple":
        return f"Explain {topic} in simple terms for beginners."
    elif detail_level == "detailed":
        return f"Provide a detailed, technical explanation of {topic}."
    else:
        return f"Explain {topic} with moderate technical detail."
