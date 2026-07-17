#!/usr/bin/env bash
# Locked-down label helper for the Marvin triage workflow.
#
# Marvin runs on untrusted issue/PR bodies from non-write users, so it must
# NOT be handed raw `gh api` (that would expose every endpoint the app token
# can reach). This helper is the ONLY GitHub write it is allowed to perform:
# it adds or removes repository labels on the one issue/PR being triaged.
#
# The target repo and number come from the environment set by the workflow —
# never from the model — and the operation is fixed to the additive labels
# endpoint (POST/DELETE /repos/{repo}/issues/{n}/labels), which works for both
# issues and PRs and cannot clobber labels applied by other workflows.
set -euo pipefail

repo="${TRIAGE_REPO:?TRIAGE_REPO not set}"
number="${TRIAGE_NUMBER:?TRIAGE_NUMBER not set}"

if [[ ! "$number" =~ ^[0-9]+$ ]]; then
  echo "TRIAGE_NUMBER must be numeric, got: $number" >&2
  exit 1
fi

op="${1:-}"
shift || true
case "$op" in
  add) method=POST ;;
  remove) method=DELETE ;;
  *)
    echo "usage: triage-label.sh <add|remove> <label>..." >&2
    exit 1
    ;;
esac

if [[ "$#" -eq 0 ]]; then
  echo "no labels given" >&2
  exit 1
fi

# Reject anything that isn't a plausible label name. Notably blocks '/' so a
# crafted value can't turn the DELETE path into a different endpoint.
label_re="^[A-Za-z0-9 ._'-]+$"
for label in "$@"; do
  if [[ ! "$label" =~ $label_re ]]; then
    echo "refusing suspicious label name: $label" >&2
    exit 1
  fi
done

# Never let triage add or remove the Require Issue Link control labels. Those
# govern PR enforcement (bypass-issue-check / trusted-contributor are sticky
# exemptions) and reopening (missing-issue-link is how closed PRs are found),
# so a prompt-injected triage run must not be able to grant an exemption or
# break recovery. Enforced here — in code — not merely in the prompt.
protected=" missing-issue-link bypass-issue-check trusted-contributor "
for label in "$@"; do
  lower="${label,,}"
  if [[ "$protected" == *" $lower "* ]]; then
    echo "refusing to touch protected control label: $label" >&2
    exit 1
  fi
done

if [[ "$method" == POST ]]; then
  args=()
  for label in "$@"; do
    args+=(-f "labels[]=$label")
  done
  gh api --method POST "/repos/${repo}/issues/${number}/labels" "${args[@]}"
else
  for label in "$@"; do
    gh api --method DELETE "/repos/${repo}/issues/${number}/labels/${label}"
  done
fi
