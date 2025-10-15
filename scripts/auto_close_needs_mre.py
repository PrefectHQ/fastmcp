#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx",
# ]
# ///
"""
Auto-close issues that need MRE (Minimal Reproducible Example).

This script runs on a schedule to automatically close issues that have been
marked as "needs MRE" and haven't received activity from the issue author
within 7 days.
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
    body: str | None


@dataclass
class Comment:
    """Represents a GitHub comment."""

    id: int
    body: str
    created_at: str
    user_id: int
    user_login: str


@dataclass
class Event:
    """Represents a GitHub issue event."""

    event: str
    created_at: str
    label_name: str | None


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

    def get_issues_with_label(
        self, label: str, page: int = 1, per_page: int = 100
    ) -> list[Issue]:
        """Fetch open issues with a specific label."""
        url = f"{self.base_url}/issues"
        issues = []

        with httpx.Client() as client:
            response = client.get(
                url,
                headers=self.headers,
                params={
                    "state": "open",
                    "labels": label,
                    "per_page": per_page,
                    "page": page,
                },
            )

            if response.status_code != 200:
                print(f"Error fetching issues: {response.status_code}")
                return issues

            data = response.json()
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
                        body=item.get("body"),
                    )
                )

        return issues

    def get_issue_events(self, issue_number: int) -> list[Event]:
        """Fetch all events for an issue."""
        url = f"{self.base_url}/issues/{issue_number}/events"
        events = []

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

                for event_data in data:
                    label_name = None
                    if event_data["event"] == "labeled" and "label" in event_data:
                        label_name = event_data["label"]["name"]

                    events.append(
                        Event(
                            event=event_data["event"],
                            created_at=event_data["created_at"],
                            label_name=label_name,
                        )
                    )

                page += 1
                if page > 10:  # Safety limit
                    break

        return events

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
                        )
                    )

                page += 1
                if page > 10:  # Safety limit
                    break

        return comments

    def get_issue_timeline(self, issue_number: int) -> list[dict]:
        """Fetch timeline events for an issue (includes issue edits)."""
        url = f"{self.base_url}/issues/{issue_number}/timeline"
        timeline = []

        with httpx.Client() as client:
            page = 1
            while True:
                response = client.get(
                    url,
                    headers={
                        **self.headers,
                        "Accept": "application/vnd.github.mockingbird-preview+json",
                    },
                    params={"page": page, "per_page": 100},
                )

                if response.status_code != 200:
                    break

                data = response.json()
                if not data:
                    break

                timeline.extend(data)

                page += 1
                if page > 10:  # Safety limit
                    break

        return timeline

    def close_issue(self, issue_number: int, comment: str) -> bool:
        """Close an issue with a comment."""
        # First add the comment
        comment_url = f"{self.base_url}/issues/{issue_number}/comments"
        with httpx.Client() as client:
            response = client.post(
                comment_url, headers=self.headers, json={"body": comment}
            )

            if response.status_code != 201:
                print(f"Failed to add comment to issue #{issue_number}")
                return False

        # Then close the issue
        issue_url = f"{self.base_url}/issues/{issue_number}"
        with httpx.Client() as client:
            response = client.patch(
                issue_url, headers=self.headers, json={"state": "closed"}
            )

            return response.status_code == 200


def find_label_application_date(
    events: list[Event], label_name: str
) -> datetime | None:
    """Find when a specific label was applied to an issue."""
    # Look for the most recent application of this label
    for event in reversed(events):
        if event.event == "labeled" and event.label_name == label_name:
            return datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
    return None


def has_author_activity_after(
    issue: Issue,
    comments: list[Comment],
    timeline: list[dict],
    after_date: datetime,
) -> bool:
    """Check if the issue author had any activity after a specific date."""
    # Check for comments from author
    for comment in comments:
        if comment.user_id == issue.user_id:
            comment_date = datetime.fromisoformat(
                comment.created_at.replace("Z", "+00:00")
            )
            if comment_date > after_date:
                print(
                    f"Issue #{issue.number}: Author commented after label application"
                )
                return True

    # Check for issue body edits from author
    for event in timeline:
        if event.get("event") == "renamed" or event.get("event") == "edited":
            if event.get("actor", {}).get("id") == issue.user_id:
                event_date = datetime.fromisoformat(
                    event["created_at"].replace("Z", "+00:00")
                )
                if event_date > after_date:
                    print(
                        f"Issue #{issue.number}: Author edited issue after label application"
                    )
                    return True

    return False


def should_close_as_needs_mre(
    issue: Issue,
    label_date: datetime,
    comments: list[Comment],
    timeline: list[dict],
) -> bool:
    """Determine if an issue should be closed for needing an MRE."""
    # Check if label is old enough (7 days)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    if label_date > seven_days_ago:
        return False

    # Check for author activity after the label was applied
    if has_author_activity_after(issue, comments, timeline, label_date):
        return False

    return True


def main():
    """Main entry point for auto-closing needs MRE issues."""
    print("[DEBUG] Starting auto-close needs MRE script")

    # Get environment variables
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "jlowin")
    repo = os.environ.get("GITHUB_REPOSITORY_NAME", "fastmcp")

    print(f"[DEBUG] Repository: {owner}/{repo}")

    # Initialize client
    client = GitHubClient(token, owner, repo)

    # Get issues with "needs MRE" label
    all_issues = []
    page = 1

    while page <= 20:  # Safety limit
        issues = client.get_issues_with_label("needs MRE", page=page)
        if not issues:
            break
        all_issues.extend(issues)
        page += 1

    print(f"[DEBUG] Found {len(all_issues)} open issues with 'needs MRE' label")

    processed_count = 0
    closed_count = 0

    for issue in all_issues:
        processed_count += 1

        if processed_count % 10 == 0:
            print(f"[DEBUG] Processed {processed_count}/{len(all_issues)} issues")

        # Get events to find when label was applied
        events = client.get_issue_events(issue.number)
        label_date = find_label_application_date(events, "needs MRE")

        if not label_date:
            print(
                f"[DEBUG] Issue #{issue.number}: Could not find label application date"
            )
            continue

        print(
            f"[DEBUG] Issue #{issue.number}: Label applied on {label_date.isoformat()}"
        )

        # Get comments and timeline
        comments = client.get_issue_comments(issue.number)
        timeline = client.get_issue_timeline(issue.number)

        # Check if we should close
        if should_close_as_needs_mre(issue, label_date, comments, timeline):
            close_message = (
                "This issue is being automatically closed because we requested a minimal reproducible example (MRE) "
                "7 days ago and haven't received a response from the issue author.\n\n"
                "**If you can provide an MRE**, please add it as a comment and we'll reopen this issue. "
                "An MRE should be a complete, runnable code snippet that demonstrates the problem.\n\n"
                "**If this was closed in error**, please leave a comment explaining the situation and we'll reopen it."
            )

            if client.close_issue(issue.number, close_message):
                print(f"[SUCCESS] Closed issue #{issue.number} (needs MRE)")
                closed_count += 1
            else:
                print(f"[ERROR] Failed to close issue #{issue.number}")

    print(f"[DEBUG] Processing complete. Closed {closed_count} issues needing MRE")


if __name__ == "__main__":
    main()
