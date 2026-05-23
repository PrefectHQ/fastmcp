from datetime import datetime, timedelta, timezone

import pytest

from scripts.auto_close_needs_mre import Comment, Issue, should_close_as_needs_mre


def make_issue(author_association: str = "NONE") -> Issue:
    return Issue(
        number=1,
        title="Bug report",
        state="open",
        created_at="2026-01-01T00:00:00Z",
        user_id=123,
        user_login="octocat",
        body="Something is broken",
        author_association=author_association,
    )


@pytest.mark.parametrize(
    "author_association",
    ["OWNER", "MEMBER", "COLLABORATOR"],
)
def test_should_not_close_maintainer_issues(author_association: str):
    label_date = datetime.now(timezone.utc) - timedelta(days=8)

    should_close = should_close_as_needs_mre(
        issue=make_issue(author_association),
        label_date=label_date,
        comments=[],
        timeline=[],
    )

    assert should_close is False


def test_should_close_non_maintainer_issue_without_author_activity():
    label_date = datetime.now(timezone.utc) - timedelta(days=8)

    should_close = should_close_as_needs_mre(
        issue=make_issue("CONTRIBUTOR"),
        label_date=label_date,
        comments=[],
        timeline=[],
    )

    assert should_close is True


def test_should_not_close_non_maintainer_issue_with_author_activity():
    label_date = datetime.now(timezone.utc) - timedelta(days=8)
    comment = Comment(
        id=1,
        body="Here's the MRE",
        created_at=(label_date + timedelta(days=1)).isoformat(),
        user_id=123,
        user_login="octocat",
    )

    should_close = should_close_as_needs_mre(
        issue=make_issue("CONTRIBUTOR"),
        label_date=label_date,
        comments=[comment],
        timeline=[],
    )

    assert should_close is False
