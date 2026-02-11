#!/bin/bash
# pr-comment.sh - Queue a structured inline review comment for the PR review
#
# Usage:
#   pr-comment.sh <file> <line> --severity <level> --title <description> --why <reason> [suggestion via stdin]
#   pr-comment.sh <file> <line> --severity <level> --title <description> --why <reason> --no-suggestion
#
# Arguments:
#   file              File path (required)
#   line              Line number (required)
#   --severity        Severity level: critical, high, medium, low, nitpick (required)
#   --title           Brief description for comment heading (required)
#   --why             One sentence explaining the risk/impact (required)
#   --no-suggestion   Explicitly skip suggestion (use for architectural issues)
#
# The suggestion code is read from stdin (use heredoc). If no stdin and no --no-suggestion, errors.
#
# Examples:
#   # With suggestion (preferred)
#   pr-comment.sh src/main.go 42 --severity high --title "Missing error check" --why "Errors are silently ignored" <<'EOF'
#   if err != nil {
#       return fmt.Errorf("operation failed: %w", err)
#   }
#   EOF
#
#   # Without suggestion (for issues requiring broader changes)
#   pr-comment.sh src/main.go 42 --severity medium --title "Consider extracting to function" \
#     --why "This logic is duplicated in 3 places" --no-suggestion
#
# Environment variables (set by the composite action):
#   PR_REVIEW_REPO          - Repository (owner/repo)
#   PR_REVIEW_PR_NUMBER     - Pull request number
#   PR_REVIEW_COMMENTS_DIR  - Directory to cache comments (default: /tmp/pr-review-comments)

set -e

# Configuration from environment
REPO="${PR_REVIEW_REPO:?PR_REVIEW_REPO environment variable is required}"
PR_NUMBER="${PR_REVIEW_PR_NUMBER:?PR_REVIEW_PR_NUMBER environment variable is required}"
COMMENTS_DIR="${PR_REVIEW_COMMENTS_DIR:-/tmp/pr-review-comments}"

# Severity emoji mapping
declare -A SEVERITY_EMOJI=(
  [critical]="🔴 CRITICAL"
  [high]="🟠 HIGH"
  [medium]="🟡 MEDIUM"
  [low]="⚪ LOW"
  [nitpick]="💬 NITPICK"
)

# Parse arguments
FILE=""
LINE=""
SEVERITY=""
TITLE=""
WHY=""
NO_SUGGESTION=false

# First two positional args are file and line
if [ $# -lt 2 ]; then
  echo "Error: file and line are required"
  echo "Usage: pr-comment.sh <file> <line> --severity <level> --title <desc> --why <reason> [<<'EOF' ... EOF]"
  exit 1
fi

FILE="$1"
LINE="$2"
shift 2

# Parse named arguments
while [ $# -gt 0 ]; do
  case "$1" in
    --severity)
      SEVERITY="$2"
      shift 2
      ;;
    --title)
      TITLE="$2"
      shift 2
      ;;
    --why)
      WHY="$2"
      shift 2
      ;;
    --no-suggestion)
      NO_SUGGESTION=true
      shift
      ;;
    *)
      echo "Error: Unknown argument: $1"
      exit 1
      ;;
  esac
done

# Read suggestion from stdin if available
SUGGESTION=""
if [ ! -t 0 ]; then
  SUGGESTION=$(cat)
fi

# Validate required arguments
if [ -z "$SEVERITY" ]; then
  echo "Error: --severity is required (critical, high, medium, low, nitpick)"
  exit 1
fi

if [ -z "$TITLE" ]; then
  echo "Error: --title is required"
  exit 1
fi

if [ -z "$WHY" ]; then
  echo "Error: --why is required"
  exit 1
fi

# Validate severity level
if [ -z "${SEVERITY_EMOJI[$SEVERITY]}" ]; then
  echo "Error: Invalid severity '$SEVERITY'. Must be one of: critical, high, medium, low, nitpick"
  exit 1
fi

# Require either suggestion or explicit --no-suggestion
if [ -z "$SUGGESTION" ] && [ "$NO_SUGGESTION" = false ]; then
  echo "Error: Suggestion required. Provide code via stdin (heredoc) or use --no-suggestion"
  echo ""
  echo "Example with suggestion:"
  echo "  pr-comment.sh file.go 42 --severity high --title \"desc\" --why \"reason\" <<'EOF'"
  echo "  fixed code here"
  echo "  EOF"
  echo ""
  echo "Example without suggestion:"
  echo "  pr-comment.sh file.go 42 --severity medium --title \"desc\" --why \"reason\" --no-suggestion"
  exit 1
fi

# Validate line is a positive integer (>= 1)
if ! [[ "$LINE" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: Line number must be a positive integer (>= 1), got: $LINE"
  exit 1
fi

# Get the diff for this file to validate the comment location
DIFF_DATA=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/files" --paginate | jq --arg f "$FILE" '.[] | select(.filename==$f)')

if [ -z "$DIFF_DATA" ]; then
  echo "Error: File '${FILE}' not found in PR diff"
  echo ""
  echo "Files changed in this PR:"
  gh api "repos/${REPO}/pulls/${PR_NUMBER}/files" --paginate --jq '.[].filename'
  exit 1
fi

PATCH=$(echo "$DIFF_DATA" | jq -r '.patch // empty')

if [ -z "$PATCH" ]; then
  echo "Error: No patch data for file '${FILE}' (file may be binary or too large)"
  exit 1
fi

# Verify the line exists in the diff
LINE_IN_DIFF=$(echo "$PATCH" | awk -v target_line="$LINE" '
BEGIN { current_line = 0; found = 0 }
/^@@/ {
  line = $0
  gsub(/.*\+/, "", line)
  gsub(/[^0-9].*/, "", line)
  current_line = line - 1
  next
}
{
  if (substr($0, 1, 1) != "-") {
    current_line++
    if (current_line == target_line) {
      found = 1
      exit
    }
  }
}
END { if (found) print "1"; else print "0" }
')

if [ "$LINE_IN_DIFF" != "1" ]; then
  echo "Error: Line ${LINE} not found in the diff for '${FILE}'"
  echo ""
  echo "Note: You can only comment on lines that appear in the diff (added, modified, or context lines)"
  echo ""
  echo "First 50 lines of diff for this file:"
  echo "$PATCH" | head -50
  exit 1
fi

# Create comments directory if it doesn't exist
mkdir -p "${COMMENTS_DIR}"

# Assemble the comment body
SEVERITY_LABEL="${SEVERITY_EMOJI[$SEVERITY]}"

BODY="**${SEVERITY_LABEL}** ${TITLE}

Why: ${WHY}"

# Add suggestion block if provided
if [ -n "$SUGGESTION" ]; then
  BODY="${BODY}

\`\`\`suggestion
${SUGGESTION}
\`\`\`"
fi

# Append standard footer
FOOTER='

---
Marvin Context Protocol | Type `/marvin` to interact further

Give us feedback! React with 🚀 if perfect, 👍 if helpful, 👎 if not.'

BODY_WITH_FOOTER="${BODY}${FOOTER}"

# Generate unique comment ID
COMMENT_ID="comment-$(date +%s)-$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
COMMENT_FILE="${COMMENTS_DIR}/${COMMENT_ID}.json"

# Create the comment JSON object
jq -n \
  --arg path "$FILE" \
  --argjson line "$LINE" \
  --arg side "RIGHT" \
  --arg body "$BODY_WITH_FOOTER" \
  --arg id "$COMMENT_ID" \
  '{
    path: $path,
    line: $line,
    side: $side,
    body: $body,
    _meta: {
      id: $id,
      file: $path,
      line: $line
    }
  }' > "${COMMENT_FILE}"

echo "✓ Queued review comment for ${FILE}:${LINE}"
echo "  Severity: ${SEVERITY_LABEL}"
echo "  Title: ${TITLE}"
echo "  Comment ID: ${COMMENT_ID}"
echo "  Comment will be submitted with pr-review.sh"
echo "  Remove with: pr-remove-comment.sh ${FILE} ${LINE}"
