# /// script
# dependencies = ["fastmcp"]
# ///

"""
FastMCP Text Me Server
--------------------------------
This defines a simple FastMCP server that sends a text message to a phone number via https://surgemsg.com/.

To run this example, create a `.env` file with the following values:

SURGE_API_KEY=...
SURGE_ACCOUNT_ID=...
SURGE_MY_PHONE_NUMBER=...
SURGE_MY_FIRST_NAME=...
SURGE_MY_LAST_NAME=...

Visit https://surgemsg.com/ and click "Get Started" to obtain these values.
"""

from typing import Annotated

import httpx
from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastmcp import FastMCP


class SurgeSettings(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_prefix="SURGE_", env_file=".env"
    )

    api_key: str
    account_id: str
    my_phone_number: Annotated[
        str, BeforeValidator(lambda v: "+" + v if not v.startswith("+") else v)
    ]
    my_first_name: str
    my_last_name: str


# Create server
mcp = FastMCP("Text me")
surge_settings = SurgeSettings()  # type: ignore


@mcp.tool(name="textme", description="Send a text message to me")
def text_me(text_content: str) -> str:
    """Send a text message to a phone number via https://surgemsg.com/"""
    with httpx.Client() as client:
        response = client.post(
            "https://api.surgemsg.com/messages",
            headers={
                "Authorization": f"Bearer {surge_settings.api_key}",
                "Surge-Account": surge_settings.account_id,
                "Content-Type": "application/json",
            },
            json={
                "body": text_content,
                "conversation": {
                    "contact": {
                        "first_name": surge_settings.my_first_name,
                        "last_name": surge_settings.my_last_name,
                        "phone_number": surge_settings.my_phone_number,
                    }
                },
            },
        )
        response.raise_for_status()
        return f"Message sent: {text_content}"
