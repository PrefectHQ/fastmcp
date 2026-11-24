#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx",
# ]
# ///
"""
Auto-close duplicate GitHub issues.

This script runs on a schedule to automatically close issues that have been
marked as duplicates and haven't received any preventing activity.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

MARVIN_BOT_ID = 225465937
# github-actions[bot] closes now; Marvin's older closures stay recognized.
ACTIONS_BOT_ID = 41898282
CLOSER_IDS = {MARVIN_BOT_ID, ACTIONS_BOT_ID}
CLOSURE_NOTICE = (
    "Closing this issue as a duplicate based on the automated analysis above."
)
ESTABLISHED_ASSOCIATIONS = {"CONTRIBUTOR", "COLLABORATOR", "MEMBER", "OWNER"}


@dataclass
class Issue:
    """Represents a GitHub issue."""

    number: int
    title: str
    state: str
    created_at: str
    user_id: int
    user_login: str


@dataclass
class Comment:
    """Represents a GitHub comment."""

    id: int
    body: str
    created_at: str
    user_id: int
    user_login: str
    user_type: str


@dataclass
class Reaction:
    """Represents a reaction on a comment."""

    user_id: int
    user_login: str
    content: str


class GitHubClient:
    """Client for interacting with GitHub API."""

    def __init__(self, token: str, owner: str, repo: str):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def get_potential_duplicate_issues(self) -> list[Issue]:
        """Fetch open issues with the potential-duplicate label."""
        url = f"{self.base_url}/issues"
        issues = []

        with httpx.Client() as client:
            page = 1
            while page <= 10:  # Safety limit
                response = client.get(
                    url,
                    headers=self.headers,
                    params={
                        "state": "open",
                        "labels": "potential-duplicate",
                        "per_page": 100,
                        "page": page,
                    },
                )

                if response.status_code != 200:
                    print(f"Error fetching issues: {response.status_code}")
                    break

                data = response.json()
                if not data:
                    break

                for item in data:
                    # Skip pull requests
                    if "pull_request" in item:
                        continue

                    issues.append(
                        Issue(
                            number=item["number"],
                            title=item["title"],
                            state=item["state"],
                            created_at=item["created_at"],
                            user_id=item["user"]["id"],
                            user_login=item["user"]["login"],
                        )
                    )

                page += 1

        return issues

    def _read_all(self, path: str) -> list[dict]:
        records = []
        with httpx.Client() as client:
            for page in range(1, 11):
                response = client.get(
                    f"{self.base_url}/{path}",
                    headers=self.headers,
                    params={"page": page, "per_page": 100},
                )
                response.raise_for_status()
                batch = response.json()
                if not isinstance(batch, list):
                    raise ValueError("Expected a GitHub collection")
                records.extend(batch)
                if "next" not in response.links:
                    return records
        raise ValueError("GitHub collection exceeded the ten-page limit")

    def get_issue_comments(self, issue_number: int) -> list[Comment]:
        return [
            Comment(
                id=comment["id"],
                body=comment["body"],
                created_at=comment["created_at"],
                user_id=comment["user"]["id"],
                user_login=comment["user"]["login"],
                user_type=comment["user"]["type"],
            )
            for comment in self._read_all(f"issues/{issue_number}/comments")
        ]

    def get_comment_reactions(self, comment_id: int) -> list[Reaction]:
        return [
            Reaction(
                user_id=reaction["user"]["id"],
                user_login=reaction["user"]["login"],
                content=reaction["content"],
            )
            for reaction in self._read_all(f"issues/comments/{comment_id}/reactions")
        ]

    def get_issue_timeline(self, issue_number: int) -> list[dict]:
        return self._read_all(f"issues/{issue_number}/timeline")

    def reopen_duplicate(self, issue_number: int, closed_at: str) -> bool:
        url = f"{self.base_url}/issues/{issue_number}"
        with httpx.Client() as client:
            response = client.get(url, headers=self.headers)
            response.raise_for_status()
            issue = response.json()
            if not is_closed_duplicate(issue) or issue["closed_at"] != closed_at:
                return False
            response = client.patch(url, headers=self.headers, json={"state": "open"})
            response.raise_for_status()
            return response.json()["state"] == "open"

    def remove_label(self, issue_number: int, label: str) -> bool:
        """Remove a label from an issue."""
        url = f"{self.base_url}/issues/{issue_number}/labels/{label}"
        with httpx.Client() as client:
            response = client.delete(url, headers=self.headers)
            return response.status_code in [200, 204]

    def close_issue(self, issue_number: int, comment: str) -> bool:
        """Close an issue with a comment and add duplicate label."""
        # First add the comment
        comment_url = f"{self.base_url}/issues/{issue_number}/comments"
        with httpx.Client() as client:
            response = client.post(
                comment_url, headers=self.headers, json={"body": comment}
            )

            if response.status_code != 201:
                print(f"Failed to add comment to issue #{issue_number}")
                return False

        # Swap labels: remove potential-duplicate, add duplicate
        self.remove_label(issue_number, "potential-duplicate")

        labels_url = f"{self.base_url}/issues/{issue_number}/labels"
        with httpx.Client() as client:
            response = client.post(
                labels_url, headers=self.headers, json={"labels": ["duplicate"]}
            )

            if response.status_code not in [200, 201]:
                print(f"Failed to add duplicate label to issue #{issue_number}")

        # Then close the issue
        issue_url = f"{self.base_url}/issues/{issue_number}"
        with httpx.Client() as client:
            response = client.patch(
                issue_url, headers=self.headers, json={"state": "closed"}
            )

            return response.status_code == 200


def find_duplicate_comment(comments: list[Comment]) -> Comment | None:
    """Find a bot comment marking the issue as duplicate."""
    for comment in comments:
        # Check for the specific duplicate message format from a bot. With
        # one duplicate the comment says "issue", not "issues".
        if (
            comment.user_type == "Bot"
            and "possible duplicate issue" in comment.body.lower()
        ):
            return comment
    return None


def was_already_auto_closed(comments: list[Comment]) -> bool:
    """Check if this issue was already auto-closed once."""
    for comment in comments:
        if (
            comment.user_type == "Bot"
            and "closing this issue as a duplicate" in comment.body.lower()
        ):
            return True
    return False


def is_past_cooldown(duplicate_comment: Comment) -> bool:
    """Check if the 3-day cooldown period has passed."""
    comment_date = datetime.fromisoformat(
        duplicate_comment.created_at.replace("Z", "+00:00")
    )
    three_days_ago = datetime.now(UTC) - timedelta(days=3)
    return comment_date <= three_days_ago


def has_human_activity(
    issue: Issue,
    duplicate_comment: Comment,
    all_comments: list[Comment],
    reactions: list[Reaction],
) -> bool:
    """Check if there's human activity that should prevent auto-closure."""
    comment_date = datetime.fromisoformat(
        duplicate_comment.created_at.replace("Z", "+00:00")
    )

    # Check for preventing reactions (thumbs down)
    for reaction in reactions:
        if reaction.content in ["-1", "confused"]:
            print(
                f"Issue #{issue.number}: Has preventing reaction from {reaction.user_login}"
            )
            return True

    # Check for any human comment after the duplicate marking
    for comment in all_comments:
        comment_date_check = datetime.fromisoformat(
            comment.created_at.replace("Z", "+00:00")
        )
        if comment_date_check > comment_date:
            if comment.user_type != "Bot":
                print(
                    f"Issue #{issue.number}: {comment.user_login} commented after duplicate marking"
                )
                return True

    return False


def is_closed_duplicate(issue: dict) -> bool:
    return (
        issue["state"] == "closed"
        and "pull_request" not in issue
        and any(label["name"] == "duplicate" for label in issue["labels"])
    )


def reconsider_duplicate(client: GitHubClient, event: dict) -> None:
    issue = event["issue"]
    comment = event["comment"]
    author = comment["user"]
    if not is_closed_duplicate(issue) or author["type"] != "User":
        return
    if (
        author["id"] != issue["user"]["id"]
        and comment["author_association"] not in ESTABLISHED_ASSOCIATIONS
    ):
        return

    closed_at = issue["closed_at"]
    if not closed_at or comment["created_at"] < closed_at:
        return
    number = issue["number"]
    closed_time = datetime.fromisoformat(closed_at)
    found_close = False
    for entry in reversed(client.get_issue_timeline(number)):
        kind, created_at = entry["event"], entry.get("created_at")
        if kind in {"closed", "reopened"}:
            if (
                found_close
                or kind != "closed"
                or created_at != closed_at
                or (entry.get("actor") or {}).get("id") not in CLOSER_IDS
            ):
                return
            found_close = True
        elif (
            found_close
            and kind == "commented"
            and (entry.get("user") or {}).get("id") in CLOSER_IDS
            and (entry.get("body") or "").startswith(CLOSURE_NOTICE)
            and created_at
            and timedelta(0)
            <= closed_time - datetime.fromisoformat(created_at)
            <= timedelta(minutes=5)
        ):
            if client.reopen_duplicate(number, closed_at):
                print(
                    f"Reopened #{number} for maintainer review after comment {comment['id']}"
                )
            return


def main():
    """Main entry point for auto-closing duplicate issues."""
    print("[DEBUG] Starting auto-close duplicates script")

    # Get environment variables
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "prefecthq")
    repo = os.environ.get("GITHUB_REPOSITORY_NAME", "fastmcp")

    print(f"[DEBUG] Repository: {owner}/{repo}")

    # Initialize client
    client = GitHubClient(token, owner, repo)
    if os.environ.get("GITHUB_EVENT_NAME") == "issue_comment":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        reconsider_duplicate(client, event)
        return

    # Only fetch issues with the potential-duplicate label
    all_issues = client.get_potential_duplicate_issues()

    print(f"[DEBUG] Found {len(all_issues)} open issues with potential-duplicate label")

    closed_count = 0
    cleared_count = 0
    failed_reads = 0

    for issue in all_issues:
        try:
            comments = client.get_issue_comments(issue.number)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            print(f"Unable to read comments for #{issue.number}: {error}")
            failed_reads += 1
            continue

        # Look for duplicate marking comment
        duplicate_comment = find_duplicate_comment(comments)
        if not duplicate_comment:
            # Label exists but no bot comment - clean up the label
            print(f"[DEBUG] Issue #{issue.number} has label but no duplicate comment")
            client.remove_label(issue.number, "potential-duplicate")
            cleared_count += 1
            continue

        # Skip if already auto-closed once (someone reopened it intentionally)
        if was_already_auto_closed(comments):
            print(
                f"[DEBUG] Issue #{issue.number} was already auto-closed, removing label"
            )
            client.remove_label(issue.number, "potential-duplicate")
            cleared_count += 1
            continue

        print(f"[DEBUG] Issue #{issue.number} has duplicate comment")

        # Still in cooldown period - skip for now, check again later
        if not is_past_cooldown(duplicate_comment):
            print(f"[DEBUG] Issue #{issue.number} still in 3-day cooldown period")
            continue

        try:
            reactions = client.get_comment_reactions(duplicate_comment.id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            print(f"Unable to read reactions for #{issue.number}: {error}")
            failed_reads += 1
            continue

        # Check for human activity that prevents closure
        if has_human_activity(issue, duplicate_comment, comments, reactions):
            print(f"[DEBUG] Issue #{issue.number} has human activity, removing label")
            client.remove_label(issue.number, "potential-duplicate")
            cleared_count += 1
            continue

        # No human activity after cooldown - close as duplicate
        close_message = (
            f"{CLOSURE_NOTICE}\n\n"
            "The duplicate issues identified contain existing discussions and potential solutions. "
            "Please add your 👍 to those issues if they match your use case.\n\n"
            "If this was closed in error, please leave a comment explaining why this is not "
            "a duplicate and we'll reopen it."
        )

        if client.close_issue(issue.number, close_message):
            print(f"[SUCCESS] Closed issue #{issue.number} as duplicate")
            closed_count += 1
        else:
            print(f"[ERROR] Failed to close issue #{issue.number}")

    print(
        f"[DEBUG] Processing complete. Closed {closed_count} duplicates, "
        f"cleared {cleared_count} from review, {failed_reads} evidence reads failed"
    )
    if failed_reads:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
