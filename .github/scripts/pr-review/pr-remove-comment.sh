#!/bin/bash
# pr-remove-comment.sh - Remove a queued review comment
#
# Usage:
#   pr-remove-comment.sh <file> <line-number>
#   pr-remove-comment.sh <comment-id>
#
# Examples:
#   pr-remove-comment.sh src/main.go 42
#   pr-remove-comment.sh comment-1234567890-1234567890
#
# This script removes a previously queued comment before it's submitted.
# Useful if the agent realizes it made a mistake or wants to update a comment.
#
# Environment variables (set by the composite action):
#   PR_REVIEW_COMMENTS_DIR - Directory containing comment files (default: /tmp/pr-review-comments)

set -e

COMMENTS_DIR="${PR_REVIEW_COMMENTS_DIR:-/tmp/pr-review-comments}"

if [ ! -d "${COMMENTS_DIR}" ]; then
  echo "No comments directory found: ${COMMENTS_DIR}"
  exit 0
fi

# Check if first argument looks like a comment ID
if [[ "$1" =~ ^comment- ]]; then
  COMMENT_ID="$1"
  COMMENT_FILE="${COMMENTS_DIR}/${COMMENT_ID}.json"
  
  if [ -f "${COMMENT_FILE}" ]; then
    FILE=$(jq -r '._meta.file // .path' "${COMMENT_FILE}")
    LINE=$(jq -r '._meta.line // .line' "${COMMENT_FILE}")
    rm -f "${COMMENT_FILE}"
    echo "✓ Removed comment ${COMMENT_ID} for ${FILE}:${LINE}"
  else
    echo "Comment not found: ${COMMENT_ID}"
    exit 1
  fi
else
  # Treat as file and line number
  FILE="$1"
  LINE="$2"
  
  if [ -z "$FILE" ] || [ -z "$LINE" ]; then
    echo "Usage:"
    echo "  pr-remove-comment.sh <file> <line-number>"
    echo "  pr-remove-comment.sh <comment-id>"
    echo ""
    echo "Examples:"
    echo "  pr-remove-comment.sh src/main.go 42"
    echo "  pr-remove-comment.sh comment-1234567890-1234567890"
    exit 1
  fi
  
  # Validate line is a positive integer (>= 1)
  if ! [[ "$LINE" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: Line number must be a positive integer (>= 1), got: $LINE"
    exit 1
  fi
  
  # Find and remove matching comment files
  # Use nullglob to handle case where no files match
  shopt -s nullglob
  REMOVED=0
  for COMMENT_FILE in "${COMMENTS_DIR}"/comment-*.json; do
    
    COMMENT_FILE_PATH=$(jq -r '._meta.file // .path' "${COMMENT_FILE}")
    COMMENT_LINE=$(jq -r '._meta.line // .line' "${COMMENT_FILE}")
    
    if [ "$COMMENT_FILE_PATH" = "$FILE" ] && [ "$COMMENT_LINE" = "$LINE" ]; then
      COMMENT_ID=$(basename "${COMMENT_FILE}" .json)
      rm -f "${COMMENT_FILE}"
      echo "✓ Removed comment ${COMMENT_ID} for ${FILE}:${LINE}"
      REMOVED=$((REMOVED + 1))
    fi
  done
  
  if [ "$REMOVED" -eq 0 ]; then
    echo "No comment found for ${FILE}:${LINE}"
    exit 1
  fi
fi
