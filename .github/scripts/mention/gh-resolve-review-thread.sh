#!/usr/bin/env bash
set -euo pipefail

# Resolve a GitHub PR review thread, optionally posting a comment first
#
# Usage:
#   gh-resolve-review-thread.sh THREAD_ID [COMMENT]
#
# Arguments:
#   THREAD_ID - The GraphQL node ID of the review thread to resolve
#   COMMENT   - Optional: Comment body to post before resolving
#
# Environment (set by composite action):
#   MENTION_REPO      - Repository (owner/repo format)
#   MENTION_PR_NUMBER - Pull request number
#   GITHUB_TOKEN      - GitHub API token
#
# Behavior:
#   1. If COMMENT is provided, posts it as a reply to the thread
#   2. Resolves the thread

# Validate required environment variables
: "${MENTION_REPO:?MENTION_REPO environment variable is required}"
: "${MENTION_PR_NUMBER:?MENTION_PR_NUMBER environment variable is required}"
THREAD_ID="${1:?Thread ID required}"
COMMENT="${2:-}"

# Step 1: Post comment if provided
if [ -n "$COMMENT" ]; then
  echo "Posting comment to thread..." >&2
  COMMENT_RESULT=$(gh api graphql -f query='
    mutation($threadId: ID!, $body: String!) {
      addPullRequestReviewThreadReply(input: {
        pullRequestReviewThreadId: $threadId,
        body: $body
      }) {
        comment {
          id
        }
      }
    }' -f threadId="$THREAD_ID" -f body="$COMMENT")
  if echo "$COMMENT_RESULT" | jq -e '.errors' > /dev/null 2>&1; then
    echo "Error posting comment: $COMMENT_RESULT" >&2
    exit 1
  fi
fi

# Step 2: Resolve the thread
echo "Resolving thread..." >&2
RESOLVE_RESULT=$(gh api graphql -f query='
  mutation($threadId: ID!) {
    resolveReviewThread(input: {threadId: $threadId}) {
      thread {
        id
        isResolved
      }
    }
  }' -f threadId="$THREAD_ID" --jq '.data.resolveReviewThread.thread')

echo "$RESOLVE_RESULT"
echo "✓ Thread resolved" >&2
