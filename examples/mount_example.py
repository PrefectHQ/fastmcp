"""Example of mounting FastMCP apps together.

This example demonstrates how to mount FastMCP apps together. It shows how to:

1. Create sub-applications for different domains
2. Mount those sub-applications to a main application
3. Access tools with prefixed names and resources with prefixed URIs
"""

import asyncio
from urllib.parse import urlparse

from fastmcp import FastMCP

# Weather sub-application
weather_app = FastMCP("Weather App")


@weather_app.tool
def get_weather_forecast(location: str) -> str:
    """Get the weather forecast for a location."""
    return f"Sunny skies for {location} today!"


@weather_app.resource(uri="weather://forecast")
async def weather_data():
    """Return current weather data."""
    return {"temperature": 72, "conditions": "sunny", "humidity": 45, "wind_speed": 5}


# News sub-application
news_app = FastMCP("News App")


@news_app.tool
def get_news_headlines() -> list[str]:
    """Get the latest news headlines."""
    return [
        "Tech company launches new product",
        "Local team wins championship",
        "Scientists make breakthrough discovery",
    ]


@news_app.resource(uri="news://headlines")
async def news_data():
    """Return latest news data."""
    return {
        "top_story": "Breaking news: Important event happened",
        "categories": ["politics", "sports", "technology"],
        "sources": ["AP", "Reuters", "Local Sources"],
    }


# Main application
app = FastMCP("Main App")


@app.tool
def check_app_status() -> dict[str, str]:
    """Check the status of the main application."""
    return {"status": "running", "version": "1.0.0", "uptime": "3h 24m"}


# Mount sub-applications
app.mount(server=weather_app, prefix="weather")

app.mount(server=news_app, prefix="news")


async def get_server_details():
    """Print information about mounted resources."""
    # Print available tools
    tools = await app.get_tools()
    print(f"\nAvailable tools ({len(tools)}):")
    for _, tool in tools.items():
        print(f"  - {tool.name}: {tool.description}")

    # Print available resources
    print("\nAvailable resources:")

    # Distinguish between native and imported resources
    # Native resources would be those directly in the main app (not prefixed)

    resources = await app.get_resources()

    native_resources = [
        uri
        for uri, _ in resources.items()
        if urlparse(uri).netloc not in ("weather", "news")
    ]

    # Imported resources - categorized by source app
    weather_resources = [
        uri for uri, _ in resources.items() if urlparse(uri).netloc == "weather"
    ]
    news_resources = [
        uri for uri, _ in resources.items() if urlparse(uri).netloc == "news"
    ]

    print(f"  - Native app resources: {native_resources}")
    print(f"  - Imported from weather app: {weather_resources}")
    print(f"  - Imported from news app: {news_resources}")


if __name__ == "__main__":
    # First run our async function to display info
    asyncio.run(get_server_details())

    # Then start the server (uncomment to run the server)
    app.run()
