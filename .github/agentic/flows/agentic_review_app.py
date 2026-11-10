from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field

from flows.agentic_github import REPOSITORY, GitHub


class ReviewApp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: int = Field(gt=0)
    slug: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    private_key_block: str = Field(pattern=r"^[a-z0-9-]{1,100}$")


@contextmanager
def review_client(
    app: ReviewApp, private_key: str, *, url: str = "https://api.github.com"
) -> Generator[GitHub]:
    import jwt

    now = int(time.time())
    assertion = jwt.encode(
        {"iat": now - 60, "exp": now + 300, "iss": str(app.app_id)},
        private_key,
        algorithm="RS256",
    )
    issuer = GitHub(assertion, url)
    identity = issuer.request("app")
    if not isinstance(identity, dict) or (identity.get("id"), identity.get("slug")) != (
        app.app_id,
        app.slug,
    ):
        raise ValueError("Reviewer App differs from its configured identity")
    installation = issuer.request(f"repos/{REPOSITORY}/installation")
    if (
        not isinstance(installation, dict)
        or installation.get("app_id") != app.app_id
        or installation.get("suspended_at") is not None
    ):
        raise ValueError("Reviewer App requires an active FastMCP installation")
    result = issuer.request(
        f"app/installations/{installation['id']}/access_tokens",
        {
            "repositories": ["fastmcp"],
            "permissions": {
                "pull_requests": "write",
                "contents": "read",
                "issues": "read",
                "checks": "read",
                "statuses": "read",
            },
        },
    )
    if not isinstance(result, dict) or not isinstance(result.get("token"), str):
        raise ValueError("GitHub did not issue a reviewer installation token")
    client = GitHub(result["token"], url)
    try:
        yield client
    finally:
        client.request("installation/token", method="DELETE")
