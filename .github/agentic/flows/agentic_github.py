from __future__ import annotations

import json
import urllib.error
import urllib.request

REPOSITORY = "PrefectHQ/fastmcp"


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
