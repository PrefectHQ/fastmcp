from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from flows.agentic_intake import HOLD
from flows.agentic_stack import StackLayer, StackSpec, restore_stack

REPOSITORY = "PrefectHQ/fastmcp"


class GitHub:
    def __init__(self, token: str, url: str = "https://api.github.com") -> None:
        self.token = token
        self.url = url.rstrip("/")

    def request(self, path: str, body: dict | None = None) -> dict | list | None:
        request = urllib.request.Request(
            self.url + "/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
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


def pr_body(layer: StackLayer) -> str:
    body = layer.body.rstrip()
    reference = rf"(?:https://github\.com/PrefectHQ/fastmcp/issues/|(?<![\w/])#){layer.issue_number}(?!\d)"
    if not re.search(reference, body, re.I):
        body += f"\n\nReferences #{layer.issue_number}."
    return (body + "\n\n" + "\n".join(layer.credits)).rstrip() + "\n"


def verify_pr(pr: dict, spec: StackSpec, layer: StackLayer, manifest: dict) -> None:
    if (
        pr.get("state") != "open"
        or pr.get("draft") is not True
        or pr.get("merged_at") is not None
        or pr.get("title") != layer.title
        or pr.get("body") != pr_body(layer)
        or pr.get("user", {}).get("id") != spec.publisher_id
        or pr.get("head", {}).get("ref") != manifest["branch"]
        or pr.get("head", {}).get("sha") != manifest["head_sha"]
        or pr.get("base", {}).get("ref") != manifest["base_ref"]
        or pr.get("base", {}).get("sha") != manifest["parent_sha"]
        or any(
            pr.get(side, {}).get("repo", {}).get("full_name", "").lower()
            != REPOSITORY.lower()
            for side in ("head", "base")
        )
    ):
        raise ValueError("Existing PR differs from the approved draft stack")
    text = "\n".join(
        [pr["title"], pr["body"], *[label["name"] for label in pr["labels"]]]
    )
    if HOLD.search(text):
        raise ValueError("Explicit hold requires maintainer review")


def verify_stack(
    stack: dict, spec: StackSpec, numbers: list[int], layers: list[dict]
) -> None:
    if (
        stack.get("open") is not True
        or stack.get("base", {}).get("ref") != spec.trunk_ref
        or [pr["number"] for pr in stack.get("pull_requests", [])] != numbers
    ):
        raise ValueError(
            "Existing stack membership or trunk differs from publication intent"
        )
    for pr, manifest in zip(stack["pull_requests"], layers, strict=True):
        if (
            pr["state"] != "open"
            or pr["draft"] is not True
            or pr["merged_at"] is not None
            or pr["head"]["sha"] != manifest["head_sha"]
            or pr["head"]["ref"] != manifest["branch"]
        ):
            raise ValueError("Existing stack layer changed")


def reconcile_stack(
    repository: Path,
    artifact: dict,
    github: GitHub,
    guard: Callable[[], None],
    push_environment: dict[str, str],
) -> dict:
    spec = restore_stack(repository, artifact)
    prefix = f"repos/{REPOSITORY}"
    published: list[tuple[StackLayer, dict]] = []

    def check() -> None:
        guard()
        trunk = github.request(f"{prefix}/git/ref/heads/{spec.trunk_ref}")
        if not isinstance(trunk, dict) or trunk["object"]["sha"] != spec.trunk_sha:
            raise ValueError("Stack trunk changed after validation")
        for layer, manifest in published:
            pr = find_pr(manifest["branch"])
            if pr is None or branch_head(manifest["branch"]) != manifest["head_sha"]:
                raise ValueError("Published stack changed during verification")
            verify_pr(pr, spec, layer, manifest)

    def branch_head(branch: str) -> str | None:
        ref = github.request(f"{prefix}/git/ref/heads/{branch}")
        if ref is None:
            return None
        if not isinstance(ref, dict):
            raise ValueError("Expected a GitHub ref")
        return ref["object"]["sha"]

    def find_pr(branch: str) -> dict | None:
        query = urllib.parse.urlencode({"state": "all", "head": "PrefectHQ:" + branch})
        matches = github.pages(f"{prefix}/pulls?{query}")
        if len(matches) > 1:
            raise ValueError("Multiple PRs exist for a publication branch")
        if not matches:
            return None
        pr = github.request(f"{prefix}/pulls/{matches[0]['number']}")
        if not isinstance(pr, dict):
            raise ValueError("Publication PR disappeared")
        return pr

    check()
    actor = github.request("user")
    if not isinstance(actor, dict) or (actor["login"], actor["id"]) != (
        spec.publisher_login,
        spec.publisher_id,
    ):
        raise ValueError("Publisher identity differs from validated commits")
    numbers = []
    links = []
    for layer, manifest in zip(spec.layers, artifact["layers"], strict=True):
        check()
        branch = manifest["branch"]
        head = branch_head(branch)
        prior = find_pr(branch)
        if prior is not None:
            verify_pr(prior, spec, layer, manifest)
            if head != manifest["head_sha"]:
                raise ValueError("Existing PR branch is missing or changed")
        elif head is not None and head != manifest["head_sha"]:
            raise ValueError("Publication branch already contains another change")
        if head is None:
            check()
            subprocess.run(
                [
                    "git",
                    "push",
                    "origin",
                    f"{manifest['head_sha']}:refs/heads/{branch}",
                ],
                cwd=repository,
                env=push_environment,
                check=True,
                capture_output=True,
                timeout=60,
            )
        if branch_head(branch) != manifest["head_sha"]:
            raise ValueError("Published branch differs from validated commit")
        if prior is None:
            check()
            prior = find_pr(branch)
            if prior is None:
                github.request(
                    f"{prefix}/pulls",
                    {
                        "title": layer.title,
                        "body": pr_body(layer),
                        "head": branch,
                        "base": manifest["base_ref"],
                        "draft": True,
                    },
                )
                prior = find_pr(branch)
        if prior is None:
            raise ValueError("GitHub did not retain the published PR")
        verify_pr(prior, spec, layer, manifest)
        numbers.append(prior["number"])
        links.append(prior["html_url"])
        published.append((layer, manifest))

    check()
    found: dict[int, dict] = {}
    for number in numbers:
        for stack in github.pages(f"{prefix}/stacks?pull_request={number}"):
            found[stack["number"]] = stack
    if len(found) > 1:
        raise ValueError("Publication PRs belong to conflicting stacks")
    if found:
        stack = next(iter(found.values()))
    else:
        check()
        stack = github.request(f"{prefix}/stacks", {"pull_requests": numbers})
        if not isinstance(stack, dict):
            raise ValueError("Expected a created GitHub stack")
    stack = github.request(f"{prefix}/stacks/{stack['number']}")
    if not isinstance(stack, dict):
        raise ValueError("GitHub did not retain the published stack")
    verify_stack(stack, spec, numbers, artifact["layers"])
    check()
    return {
        "status": "published",
        "stack_number": stack["number"],
        "stack_api_url": stack["url"],
        "pull_requests": numbers,
        "pr_urls": links,
        "spec_sha256": artifact["spec_sha256"],
        "bundle_sha256": artifact["bundle_sha256"],
    }
