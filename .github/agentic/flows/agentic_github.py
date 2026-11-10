from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

REPOSITORY = "PrefectHQ/fastmcp"
WRITE_ROLES = frozenset({"admin", "maintain", "write"})


def can_write(read: Callable[[str], object], login: str) -> bool:
    """Whether `login` has write, maintain or admin access to the repository."""
    grant = read(f"repos/{REPOSITORY}/collaborators/{login}/permission")
    if not isinstance(grant, dict):
        raise ValueError("Invalid collaborator permission")
    # `permission` folds maintain into write; `role_name` names the actual role.
    return (
        grant.get("role_name") in WRITE_ROLES or grant.get("permission") in WRITE_ROLES
    )


class GitHub:
    def __init__(self, token: str, url: str = "https://api.github.com") -> None:
        self.token = token
        self.url = url.rstrip("/")

    def request(
        self, path: str, body: dict | None = None, *, method: str | None = None
    ) -> dict | list | None:
        request = urllib.request.Request(
            self.url + "/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return None if response.status == 204 else json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and body is None:
                return None
            raise

    def pages(self, path: str) -> list[dict]:
        result = []
        for page in range(1, 11):
            separator = "&" if "?" in path else "?"
            batch = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Expected a complete GitHub collection")
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise ValueError("GitHub collection exceeded the publication bound")
