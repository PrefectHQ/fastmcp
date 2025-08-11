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

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx


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

    def get_open_issues(
        self, created_before: datetime, page: int = 1, per_page: int = 100
    ) -> list[Issue]:
        """Fetch open issues created before a certain date."""
        url = f"{self.base_url}/issues"
        issues = []

        with httpx.Client() as client:
            response = client.get(
                url,
                headers=self.headers,
                params={"state": "open", "per_page": per_page, "page": page},
            )

            if response.status_code != 200:
                print(f"Error fetching issues: {response.status_code}")
                return issues

            data = response.json()
            for item in data:
                # Skip pull requests
                if "pull_request" in item:
                    continue

                created_at = datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                )
                if created_at <= created_before:
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

        return issues

    def get_issue_comments(self, issue_number: int) -> list[Comment]:
        """Fetch all comments for an issue."""
        url = f"{self.base_url}/issues/{issue_number}/comments"
        comments = []

        with httpx.Client() as client:
            page = 1
            while True:
                response = client.get(
                    url, headers=self.headers, params={"page": page, "per_page": 100}
                )

                if response.status_code != 200:
                    break

                data = response.json()
                if not data:
                    break

                for comment_data in data:
                    comments.append(
                        Comment(
                            id=comment_data["id"],
                            body=comment_data["body"],
                            created_at=comment_data["created_at"],
                            user_id=comment_data["user"]["id"],
                            user_login=comment_data["user"]["login"],
                            user_type=comment_data["user"]["type"],
                        )
                    )

                page += 1
                if page > 10:  # Safety limit
                    break

        return comments

    def get_comment_reactions(
        self, issue_number: int, comment_id: int
    ) -> list[Reaction]:
        """Fetch reactions for a specific comment."""
        url = f"{self.base_url}/issues/{issue_number}/comments/{comment_id}/reactions"
        reactions = []

        with httpx.Client() as client:
            response = client.get(url, headers=self.headers)

            if response.status_code != 200:
                return reactions

            data = response.json()
            for reaction_data in data:
                reactions.append(
                    Reaction(
                        user_id=reaction_data["user"]["id"],
                        user_login=reaction_data["user"]["login"],
                        content=reaction_data["content"],
                    )
                )

        return reactions

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

        # Add the duplicate label
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
        # Check for the specific duplicate message format from a bot
        if (
            comment.user_type == "Bot"
            and "possible duplicate issues" in comment.body.lower()
        ):
            return comment
    return None


def should_close_as_duplicate(
    issue: Issue,
    duplicate_comment: Comment,
    all_comments: list[Comment],
    reactions: list[Reaction],
) -> bool:
    """Determine if an issue should be closed as duplicate."""

    # Check if comment is old enough (3 days)
    comment_date = datetime.fromisoformat(
        duplicate_comment.created_at.replace("Z", "+00:00")
    )
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)

    if comment_date > three_days_ago:
        return False

    # Check for preventing reactions (thumbs down)
    for reaction in reactions:
        if reaction.content in ["-1", "confused"]:
            print(
                f"Issue #{issue.number}: Has preventing reaction from {reaction.user_login}"
            )
            return False

    # Check for user activity after the duplicate comment
    for comment in all_comments:
        comment_date_check = datetime.fromisoformat(
            comment.created_at.replace("Z", "+00:00")
        )
        if comment_date_check > comment_date:
            # Issue author commented after duplicate marking
            if comment.user_id == issue.user_id:
                print(
                    f"Issue #{issue.number}: Author commented after duplicate marking"
                )
                return False

    return True


def main():
    """Main entry point for auto-closing duplicate issues."""
    print("[DEBUG] Starting auto-close duplicates script")

    # Get environment variables
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "jlowin")
    repo = os.environ.get("GITHUB_REPOSITORY_NAME", "fastmcp")

    print(f"[DEBUG] Repository: {owner}/{repo}")

    # Initialize client
    client = GitHubClient(token, owner, repo)

    # Get issues created more than 3 days ago
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)

    all_issues = []
    page = 1

    while page <= 20:  # Safety limit
        issues = client.get_open_issues(three_days_ago, page=page)
        if not issues:
            break
        all_issues.extend(issues)
        page += 1

    print(f"[DEBUG] Found {len(all_issues)} open issues created more than 3 days ago")

    processed_count = 0
    closed_count = 0

    for issue in all_issues:
        processed_count += 1

        if processed_count % 10 == 0:
            print(f"[DEBUG] Processed {processed_count}/{len(all_issues)} issues")

        # Get comments for this issue
        comments = client.get_issue_comments(issue.number)

        # Look for duplicate marking comment
        duplicate_comment = find_duplicate_comment(comments)
        if not duplicate_comment:
            continue

        print(f"[DEBUG] Issue #{issue.number} has duplicate comment")

        # Get reactions on the duplicate comment
        reactions = client.get_comment_reactions(issue.number, duplicate_comment.id)

        # Check if we should close
        if should_close_as_duplicate(issue, duplicate_comment, comments, reactions):
            close_message = (
                "Closing this issue as a duplicate based on the automated analysis above.\n\n"
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

    print(f"[DEBUG] Processing complete. Closed {closed_count} duplicate issues")


if __name__ == "__main__":
    main()
