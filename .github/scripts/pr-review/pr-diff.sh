#!/bin/bash
# pr-diff.sh - Show changed files or diff for a specific file
#
# Usage: 
#   pr-diff.sh           - List all changed files (shows full diff if small enough)
#   pr-diff.sh <file>    - Show diff for a specific file with line numbers
#
# Environment variables (set by the composite action):
#   PR_REVIEW_REPO       - Repository (owner/repo)
#   PR_REVIEW_PR_NUMBER  - Pull request number

set -e

# Configuration from environment
REPO="${PR_REVIEW_REPO:?PR_REVIEW_REPO environment variable is required}"
PR_NUMBER="${PR_REVIEW_PR_NUMBER:?PR_REVIEW_PR_NUMBER environment variable is required}"
EXPECTED_HEAD="${PR_REVIEW_HEAD_SHA:-}"

# Check if HEAD has changed since review started (race condition detection)
if [ -n "$EXPECTED_HEAD" ]; then
  CURRENT_HEAD=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}" --jq '.head.sha')
  if [ "$CURRENT_HEAD" != "$EXPECTED_HEAD" ]; then
    echo "⚠️  WARNING: PR head has changed since review started!"
    echo "   Review started at: ${EXPECTED_HEAD:0:7}"
    echo "   Current head:      ${CURRENT_HEAD:0:7}"
    echo "   Line numbers below may not match the commit being reviewed."
    echo ""
  fi
fi

# Thresholds for "too big" - show file list only if exceeded
MAX_FILES=25
MAX_TOTAL_LINES=1500

FILE="$1"

# Function to add line numbers to a patch
# Format: [LINE] +added | [LINE]  context | [----] -deleted
add_line_numbers() {
  awk '
  BEGIN { new_line = 0 }
  /^@@/ {
    # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
    match($0, /\+([0-9]+)/)
    new_line = substr($0, RSTART+1, RLENGTH-1) - 1
    print ""
    print $0
    next
  }
  /^-/ {
    # Deleted line - cannot comment on these
    printf "[----] %s\n", $0
    next
  }
  /^\+/ {
    # Added line - can comment, show line number
    new_line++
    printf "[%4d] %s\n", new_line, $0
    next
  }
  {
    # Context line (space prefix) - can comment, show line number
    new_line++
    printf "[%4d] %s\n", new_line, $0
  }
  '
}

if [ -z "$FILE" ]; then
  # Get file list with stats
  FILES_DATA=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/files" --paginate)
  
  FILE_COUNT=$(echo "$FILES_DATA" | jq 'length')
  TOTAL_ADDITIONS=$(echo "$FILES_DATA" | jq '[.[].additions] | add // 0')
  TOTAL_DELETIONS=$(echo "$FILES_DATA" | jq '[.[].deletions] | add // 0')
  TOTAL_LINES=$((TOTAL_ADDITIONS + TOTAL_DELETIONS))
  
  echo "PR #${PR_NUMBER} Summary: ${FILE_COUNT} files changed (+${TOTAL_ADDITIONS}/-${TOTAL_DELETIONS})"
  echo ""
  
  # Check if diff is too large
  if [ "$FILE_COUNT" -gt "$MAX_FILES" ] || [ "$TOTAL_LINES" -gt "$MAX_TOTAL_LINES" ]; then
    echo "⚠️  Large diff detected (>${MAX_FILES} files or >${MAX_TOTAL_LINES} lines changed)"
    echo "   Review files individually using: pr-diff.sh <filename>"
    echo ""
    echo "Files changed:"
    echo "$FILES_DATA" | jq -r '.[] | "  \(.filename) (+\(.additions)/-\(.deletions))"'
  else
    # Small enough - show all diffs with line numbers
    echo "Files changed:"
    echo "$FILES_DATA" | jq -r '.[] | "  \(.filename) (+\(.additions)/-\(.deletions))"'
    echo ""
    echo "─────────────────────────────────────────────────────────────────────"
    echo ""
    
    # Show each file's diff by iterating over indices
    for i in $(seq 0 $((FILE_COUNT - 1))); do
      FNAME=$(echo "$FILES_DATA" | jq -r ".[$i].filename")
      PATCH=$(echo "$FILES_DATA" | jq -r ".[$i].patch // empty")
      
      if [ -n "$PATCH" ]; then
        echo "## ${FNAME}"
        echo "Use: pr-comment.sh ${FNAME} <LINE> --severity <level> --title \"desc\" --why \"reason\" <<'EOF' ... EOF"
        echo "Format: [LINE] +added | [LINE] context | [----] -deleted (can't comment)"
        echo "$PATCH" | add_line_numbers
        echo ""
        echo "─────────────────────────────────────────────────────────────────────"
        echo ""
      fi
    done
  fi
else
  # Show specific file diff
  PATCH=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/files" --paginate --jq --arg file "$FILE" '.[] | select(.filename==$file) | .patch')
  
  if [ -z "$PATCH" ]; then
    echo "Error: File '${FILE}' not found in PR diff"
    echo ""
    echo "Files changed in this PR:"
    gh api "repos/${REPO}/pulls/${PR_NUMBER}/files" --paginate --jq '.[].filename'
    exit 1
  fi
  
  echo "## ${FILE}"
  echo "Use: pr-comment.sh ${FILE} <LINE> --severity <level> --title \"desc\" --why \"reason\" <<'EOF' ... EOF"
  echo "Format: [LINE] +added | [LINE] context | [----] -deleted (can't comment)"
  echo "$PATCH" | add_line_numbers
fi
