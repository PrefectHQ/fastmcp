#!/bin/bash
# pr-review.sh - Submit a PR review (approve, request changes, or comment)
#
# Usage: pr-review.sh <APPROVE|REQUEST_CHANGES|COMMENT> [review-body]
# Example: pr-review.sh REQUEST_CHANGES "Please fix the issues noted above"
#
# This script creates and submits a review with any queued inline comments.
# Comments are read from individual files in PR_REVIEW_COMMENTS_DIR (created by pr-comment.sh).
#
# The review body can contain special characters (backticks, dollar signs, etc.)
# and will be safely passed to the GitHub API without shell interpretation.
#
# Environment variables (set by the composite action):
#   PR_REVIEW_REPO          - Repository (owner/repo)
#   PR_REVIEW_PR_NUMBER     - Pull request number
#   PR_REVIEW_HEAD_SHA      - HEAD commit SHA
#   PR_REVIEW_COMMENTS_DIR  - Directory containing queued comment files (default: /tmp/pr-review-comments)

set -e

# Configuration from environment
REPO="${PR_REVIEW_REPO:?PR_REVIEW_REPO environment variable is required}"
PR_NUMBER="${PR_REVIEW_PR_NUMBER:?PR_REVIEW_PR_NUMBER environment variable is required}"
HEAD_SHA="${PR_REVIEW_HEAD_SHA:?PR_REVIEW_HEAD_SHA environment variable is required}"
COMMENTS_DIR="${PR_REVIEW_COMMENTS_DIR:-/tmp/pr-review-comments}"

# Arguments
EVENT="$1"
shift 2>/dev/null || true

# Read body from remaining arguments
# Join all remaining arguments with spaces, preserving the string as-is
BODY="$*"

if [ -z "$EVENT" ]; then
  echo "Usage: pr-review.sh <APPROVE|REQUEST_CHANGES|COMMENT> [review-body]"
  echo "Example: pr-review.sh REQUEST_CHANGES 'Please fix the issues noted in the inline comments'"
  exit 1
fi

# Validate event type
case "$EVENT" in
  APPROVE|REQUEST_CHANGES|COMMENT)
    ;;
  *)
    echo "Error: Invalid event type '${EVENT}'"
    echo "Must be one of: APPROVE, REQUEST_CHANGES, COMMENT"
    exit 1
    ;;
esac

# Read queued comments from individual files
COMMENTS="[]"
COMMENT_COUNT=0

if [ -d "${COMMENTS_DIR}" ]; then
  # Collect all comment files and merge into a single JSON array
  # Remove _meta fields before submitting (they're only for internal use)
  COMMENT_FILES=("${COMMENTS_DIR}"/comment-*.json)
  
  if [ -f "${COMMENT_FILES[0]}" ]; then
    # Use jq to read all comment files, extract the comment data (without _meta), and combine
    COMMENTS=$(jq -s '[.[] | del(._meta)]' "${COMMENTS_DIR}"/comment-*.json)
    COMMENT_COUNT=$(echo "$COMMENTS" | jq 'length')
    if [ "$COMMENT_COUNT" -gt 0 ]; then
      echo "Found ${COMMENT_COUNT} queued inline comment(s)"
    fi
  fi
fi

# Append standard footer to the review body (if body is provided)
FOOTER='

---
Marvin Context Protocol | Type `/marvin` to interact further

Give us feedback! React with 🚀 if perfect, 👍 if helpful, 👎 if not.'

if [ -n "$BODY" ]; then
  BODY_WITH_FOOTER="${BODY}${FOOTER}"
else
  BODY_WITH_FOOTER=""
fi

# Build the review request JSON
# Use jq to safely construct the JSON with all special characters handled
REVIEW_JSON=$(jq -n \
  --arg commit_id "$HEAD_SHA" \
  --arg event "$EVENT" \
  --arg body "$BODY_WITH_FOOTER" \
  --argjson comments "$COMMENTS" \
  '{
    commit_id: $commit_id,
    event: $event,
    comments: $comments
  } + (if $body != "" then {body: $body} else {} end)')

# Check if HEAD has changed since review started (race condition detection)
CURRENT_HEAD=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}" --jq '.head.sha')
if [ "$CURRENT_HEAD" != "$HEAD_SHA" ]; then
  echo "⚠️  WARNING: PR head has changed since review started!"
  echo "   Review started at: ${HEAD_SHA:0:7}"
  echo "   Current head:      ${CURRENT_HEAD:0:7}"
  echo ""
  echo "   New commits may have shifted line numbers. Review will be submitted"
  echo "   against the original commit (${HEAD_SHA:0:7}) but comments may be outdated."
  echo ""
fi

echo "Submitting ${EVENT} review for commit ${HEAD_SHA:0:7}..."

# Create and submit the review in one API call
# Use a temp file to safely pass the JSON body
TEMP_JSON=$(mktemp)
trap "rm -f ${TEMP_JSON}" EXIT
echo "$REVIEW_JSON" > "${TEMP_JSON}"

RESPONSE=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/reviews" \
  -X POST \
  --input "${TEMP_JSON}" 2>&1) || {
  echo "Error submitting review:"
  echo "$RESPONSE"
  exit 1
}

# Clean up the comments directory after successful submission
if [ -d "${COMMENTS_DIR}" ] && [ "$COMMENT_COUNT" -gt 0 ]; then
  rm -f "${COMMENTS_DIR}"/comment-*.json
  # Remove directory if empty
  rmdir "${COMMENTS_DIR}" 2>/dev/null || true
fi

REVIEW_URL=$(echo "$RESPONSE" | jq -r '.html_url // empty')
REVIEW_STATE=$(echo "$RESPONSE" | jq -r '.state // empty')

if [ -n "$REVIEW_URL" ]; then
  echo "✓ Review submitted (${REVIEW_STATE}): ${REVIEW_URL}"
  if [ "$COMMENT_COUNT" -gt 0 ]; then
    echo "  Included ${COMMENT_COUNT} inline comment(s)"
  fi
else
  echo "✓ Review submitted successfully"
fi
