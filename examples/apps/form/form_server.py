"""Form input — collect structured data from users via Pydantic models.

Usage:
    uv run python form_server.py
"""

from typing import Literal

from pydantic import BaseModel, Field

from fastmcp import FastMCP
from fastmcp.apps.form import FormInput


class ShippingAddress(BaseModel):
    name: str = Field(description="Full name")
    street: str = Field(description="Street address")
    city: str
    state: str = Field(description="Two-letter state code")
    zip_code: str = Field(description="5-digit ZIP")


class BugReport(BaseModel):
    title: str = Field(description="Brief summary")
    severity: Literal["low", "medium", "high", "critical"]
    description: str = Field(
        description="Detailed description",
        json_schema_extra={"ui": {"type": "textarea"}},
    )


mcp = FastMCP(
    "Form Demo",
    providers=[
        FormInput(model=ShippingAddress),
        FormInput(model=BugReport),
    ],
)

if __name__ == "__main__":
    mcp.run()
