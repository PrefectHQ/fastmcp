# Review maintainer decision stacks

Use this guidance when preparing or reviewing a batch of maintainer actions. A
decision stack is a group of explicit proposals with their evidence, review, and
dependencies. It may include assignment, a PR review, a draft fix, or a proposed
closure. Order dependent actions; independent proposals can be reviewed together.
An ordinary single-issue task still uses its existing skill.

## Roles and context

The **Elephant** is the maintainer-facing agent that retains project context,
prior decisions, accepted risks, and the maintainer's authorization. It assembles
the proposals, evaluates objections, and helps the maintainer decide. Preserve
this context in a durable decision record when work spans sessions.

**Goldfish** are independent reviewers in fresh contexts without the proposing
agent's conversation. Give them the proposal, exact source revisions, relevant
project skills, raw evidence, prior reviews and replies, and recorded maintainer
decisions. Fresh context removes conversational bias, not relevant history. A
different model or a second pass in the same conversation is not independent
review. Investigators and patch authors can also run in isolated contexts, but
their self-review does not substitute for a Goldfish review.

Agentic nodes perform only their assigned role and return its declared output.
Shared skills do not authorize spawning reviewers, acquiring new credentials,
publishing, or executing a recommendation. If the role cannot run checks or
obtain independent review, report that limitation. Do not manufacture a completed
review or ask an investigator to act as the maintainer-facing agent.

## Prepare an actionable proposal

For each item, retain the target and exact action or patch; why FastMCP wants it;
the relevant issue/PR, head and baseline revisions; evidence links and executed
checks with results and limitations; contributor ownership and credit; and any
dependencies or execution preconditions. Include the proposed public wording
when the action would post a comment. Reuse the workflow's structured fields
when supplied rather than inventing a second output format.

Separate evidence gathered, independent review completed, human approval, and
action executed. A successful run, a posted review, or model agreement does not
establish maintainer acceptance or merge readiness. Retain confirmed publication
receipts even when a later check blocks further work.

## Review the items and their composition

Use the applicable issue, PR, testing, documentation, or security skill for each
item. Choose review lenses that address its actual risk: intended behavior and
compatibility, executed reproduction and correctness, or contribution ownership
and project policy. Collect consequential findings across the full proposal in
one pass. A reviewer that failed, timed out, or returned no usable report did not
complete the review; record it as unavailable, not as finding no problems.

For a batch, also inspect interactions: competing fixes, shared causes, dependent
patches, contradictory assignment/closure proposals, and whether each canonical
issue or PR survives the other proposed actions. Check any proposed merge
composition under the existing review-pr and contribution policies. Record which
items are ready for a maintainer decision and which remain blocked; an unrelated
blocked item need not prevent review of the rest.

## Resolve findings and obtain sign-off

Keep each finding's identity, reviewer, affected revision/scope, evidence, and
disposition. The Elephant can recommend a fix, rebut it with evidence, or present
a concrete risk for the maintainer to accept. Preserve unresolved disagreement;
another model's approval does not dispose of it. Do not reopen a settled objection
without changed evidence. Recheck a claimed fix against the new revision, and
stop at the assigned review budget with remaining findings explicit rather than
iterating until a reviewer says yes.

Present a concise batch with the proposed actions, dependencies, remaining
objections, and the decisions needed from the maintainer. Record approval against
the selected items and their exact scope, including any accepted risks. Approval
of one item does not authorize others or a changed patch. Existing explicit
authorization remains valid within its scope; do not ask for it again.

Before an authorized write, recheck the relevant preconditions: target revisions,
ownership, holds, canonical references, and any required checks. A material change
returns the affected item for review. Execute only approved actions through the
available authorized mechanism, then read back the result and retain its receipt.
Where no executor exists, supply the concrete approved action for manual handling;
do not describe instructions as an implemented approval queue or executor. Keep
proposed, approved, executed, and accepted outcomes distinct when reporting progress.
