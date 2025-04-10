"""
Modular FastMCP Application Example

This example demonstrates building a modular application with FastMCP
by separating functionality into domain-specific modules.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from fastmcp import Context, FastMCP

# ----- DATA MODULE -----
data_app = FastMCP("Data Module")

# Simulated database
users_db = [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob", "email": "bob@example.com"},
    {"id": 3, "name": "Charlie", "email": "charlie@example.com"},
]


@data_app.resource("users://all")
def get_all_users() -> List[Dict[str, Any]]:
    """Get all users in the database"""
    return users_db


@data_app.resource("users://{user_id}")
def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Get a specific user by ID"""
    user_id_int = int(user_id)
    for user in users_db:
        if user["id"] == user_id_int:
            return user
    return None


@data_app.tool()
async def create_user(name: str, email: str, ctx: Context) -> Dict[str, Any]:
    """Add a new user to the database"""
    # Simulate a slow operation
    await ctx.info(f"Creating user {name}...")
    await asyncio.sleep(1)

    # Create user
    new_id = max(user["id"] for user in users_db) + 1
    new_user = {"id": new_id, "name": name, "email": email}
    users_db.append(new_user)

    await ctx.info(f"User created with ID {new_id}")
    return new_user


# ----- ANALYTICS MODULE -----
analytics_app = FastMCP("Analytics Module")


@analytics_app.tool()
async def analyze_users(ctx: Context) -> Dict[str, Any]:
    """Run analytics on user data"""
    # Get user data from the data module
    users = await ctx.read_resource("data:users://all")

    # Perform analytics
    await ctx.info("Analyzing user data...")
    await asyncio.sleep(1)

    # Return analytics results
    return {
        "total_users": len(users),
        "domains": {user["email"].split("@")[1] for user in users},
    }


@analytics_app.resource("analytics://summary")
def get_analytics_summary() -> Dict[str, Any]:
    """Get a summary of analytics data"""
    return {"active_users": len(users_db), "last_updated": "2023-06-01"}


# ----- FILESYSTEM MODULE -----
files_app = FastMCP("Filesystem Module")


@files_app.resource("files://desktop")
def list_desktop_files() -> List[str]:
    """List files on the user's desktop"""
    desktop = Path.home() / "Desktop"
    return [f.name for f in desktop.iterdir() if f.is_file()]


@files_app.tool()
async def search_files(query: str, ctx: Context) -> List[str]:
    """Search for files matching a query"""
    await ctx.info(f"Searching for files matching '{query}'...")

    # Simulate a file search
    desktop = Path.home() / "Desktop"
    files = [
        f.name
        for f in desktop.iterdir()
        if f.is_file() and query.lower() in f.name.lower()
    ]

    await ctx.info(f"Found {len(files)} matching files")
    return files


# ----- MAIN APPLICATION -----
# Create the main application that combines all modules
main_app = FastMCP("Modular FastMCP Demo")


@main_app.tool()
async def get_system_info(ctx: Context) -> Dict[str, Any]:
    """Get comprehensive system information"""
    await ctx.info("Gathering system information...")

    # Use the mounted modules to gather info
    users = await ctx.read_resource("data:users://all")
    analytics = await ctx.read_resource("analytics:analytics://summary")
    desktop_files = await ctx.read_resource("files:files://desktop")

    return {
        "users": {"count": len(users), "names": [user["name"] for user in users]},
        "analytics": analytics,
        "files": {"desktop_count": len(desktop_files)},
    }


# Mount all modules to the main app
main_app.mount("data", data_app)
main_app.mount("analytics", analytics_app)
main_app.mount("files", files_app)

if __name__ == "__main__":
    # Now register resources (which requires async)
    async def initialize_resources():
        await main_app.register_all_mounted_resources()
        print("Resources registered successfully!")

    # Initialize resources
    asyncio.run(initialize_resources())

    # Start the server
    print("Starting modular FastMCP application...")
    main_app.run()
