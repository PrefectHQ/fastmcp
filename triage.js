"use strict";

const stage = (name, owner, detail, state = "done", label = "Recorded") => ({
  name,
  owner,
  detail,
  state,
  label,
});
const intake = stage(
  "Intake",
  "GitHub state",
  "Read the issue, contribution history, ownership, and holds. Existing contributors’ work takes priority over a competing implementation.",
);
const assess = stage(
  "Assess",
  "Triage agent",
  "Establish the intended behavior from the project’s docs, source, history, and maintainer decisions. Select a review, reproduction, proposal, or decision lane.",
);
const verify = stage(
  "Verify",
  "Isolated worker",
  "Execute selected tests against the baseline and proposed PR head. Retain the exact revisions, commands, results, and limitations; passing contributor tests alone is not merge readiness.",
);
const review = stage(
  "Review",
  "Independent reviewer",
  "Challenge the assessment and executed evidence. Check supported behavior, compatibility, prior discussion, and concrete next actions.",
);

const cases = {
  delivered: {
    title: "An existing contribution receives a review",
    summary:
      "Issue #5926 led to executable verification and an independent review of PR #5927. Publication and final freshness both completed.",
    status: "Completed",
    tone: "positive",
    selected: 4,
    steps: [
      intake,
      assess,
      verify,
      review,
      stage(
        "Publish",
        "Reviewer App",
        "Post a COMMENT review on the exact PR head. Read back the GitHub review and retain its identity. This records findings; it does not approve merging.",
        "done",
        "Review posted",
      ),
      stage(
        "Freshness",
        "Final intake check",
        "Confirm that relevant evidence remains current. This example committed the issue as processed with no retained operational errors.",
        "done",
        "Accepted by intake",
      ),
    ],
    facts: [
      ["Execution", "Finished", "positive"],
      ["GitHub output", "Review posted", "positive"],
      ["Evidence", "Current at completion", "positive"],
      ["Acceptance", "Not merge approval", "neutral"],
    ],
    receiptTitle: "The review is on GitHub",
    receiptText:
      "PR #5927 · revision-specific comment from fastmcp-triage-reviewer[bot]",
    url: "https://github.com/PrefectHQ/fastmcp/pull/5927#pullrequestreview-5692457714",
    caveat:
      "Recorded October 29, 2026 (UTC). Verification observed four failing baseline cases, 24 passing head tests, and 227 passing adjacent tests. The review still identifies readiness work; no PR was merged by this run.",
  },
  stale: {
    title: "A review was posted, then its result became stale",
    summary:
      "The review on PR #5588 created GitHub cross-references. Final freshness treated those publication effects as new evidence and marked intake Degraded.",
    status: "Degraded",
    tone: "attention",
    selected: 5,
    steps: [
      intake,
      assess,
      verify,
      review,
      stage(
        "Publish",
        "Reviewer App",
        "The App successfully posted its review on canonical PR #5588. It retained a verified receipt. The controller also recorded a proposal for finishing the contribution.",
        "done",
        "Review posted",
      ),
      stage(
        "Freshness",
        "Final intake check",
        "The posted review mentioned the issue and earlier PRs. GitHub added four cross-reference events; the final check mistook these own-review effects for new evidence. Intake kept the issue pending. This is a known self-invalidation defect.",
        "attention",
        "Refresh required",
      ),
    ],
    facts: [
      ["Execution", "Finished", "positive"],
      ["GitHub output", "Review posted", "positive"],
      ["Evidence", "Marked stale", "attention"],
      ["Acceptance", "Further action blocked", "attention"],
    ],
    receiptTitle: "The posted review still exists",
    receiptText:
      "PR #5588 · the later freshness stop does not undo this public output",
    url: "https://github.com/PrefectHQ/fastmcp/pull/5588#pullrequestreview-5692456169",
    caveat:
      "Recorded October 29, 2026 (UTC). Source and baseline did not change in the diagnostic comparison. A finish-PR recommendation was retained, but final intake marked the evidence stale. The proposal needs rechecking before action. No automatic closure or merge occurred.",
  },
  blocked: {
    title: "Verification finished, but required evidence could not be loaded",
    summary:
      "In an earlier handoff incident, verification retained its evidence. The next lookup returned no record, so independent review could not proceed.",
    status: "Degraded",
    tone: "attention",
    selected: 3,
    steps: [
      intake,
      assess,
      verify,
      stage(
        "Review",
        "Independent review handoff",
        "The evidence artifact existed, but the immediate owner/key lookup returned no record. The controller stopped before invoking independent review. A durable artifact alone did not establish a completed handoff.",
        "blocked",
        "Evidence unavailable",
      ),
      stage(
        "Publish",
        "Reviewer App",
        "This attempt did not reach review publication. A later manually merged PR is not evidence that this failed attempt delivered a review.",
        "unreached",
        "Not reached",
      ),
      stage(
        "Freshness",
        "Final intake check",
        "The earlier operational failure remains part of the run’s history. A retry must first check retained work and actual GitHub effects.",
        "unreached",
        "Not reached",
      ),
    ],
    facts: [
      ["Execution", "Finished with a stop", "attention"],
      ["GitHub output", "No review from this attempt", "blocked"],
      ["Evidence", "Retained, handoff failed", "attention"],
      ["Acceptance", "Not established", "neutral"],
    ],
    receiptTitle: "No public review receipt for this attempt",
    receiptText:
      "Operational evidence was retained; independent review and publication did not complete.",
    url: null,
    caveat:
      "Historical incident from October 28, 2026. The later repair passes the evidence directly after its durable write. This example explains the failure mode; it does not claim the old attempt recovered or identify a contributor defect.",
  },
  draft: {
    title: "An unclaimed issue becomes a reviewed draft fix",
    summary:
      "Issue #6139 went through reproduction, patch preparation, validation, and independent review before the workflow published draft PR #6141.",
    status: "Draft published",
    tone: "positive",
    selected: 6,
    steps: [
      intake,
      stage(
        "Reproduce",
        "Investigation agent",
        "Establish a supported defect and retain a regression that fails on the unchanged baseline for the claimed reason.",
      ),
      stage(
        "Prepare",
        "Patch agent",
        "Prepare a scoped patch with issue-author credit. Recheck ownership and avoid replacing a contributor’s existing work.",
      ),
      stage(
        "Validate",
        "Isolated worker",
        "Execute the patch and applicable checks on its recorded base. Retain exact patch bytes, revisions, and executed results.",
      ),
      stage(
        "Review",
        "Independent patch reviewer",
        "Review the validated patch for intended behavior, compatibility, correctness, and remaining limitations.",
      ),
      stage(
        "Preflight",
        "Publication guard",
        "Recheck ownership, discussion, holds, and revisions before publication and at write boundaries. Validation and independent review must already have passed.",
        "done",
        "Evidence checked",
      ),
      stage(
        "Publish",
        "Configured publisher",
        "Publish the draft PR, reconcile uncertain writes, and retain the verified GitHub identity. Publishing a draft does not authorize marking it ready or merging it.",
        "done",
        "Draft PR created",
      ),
    ],
    facts: [
      ["Execution", "Finished", "positive"],
      ["GitHub output", "Draft PR published", "positive"],
      ["Evidence", "Validation retained", "positive"],
      ["Acceptance", "Maintainer decision", "neutral"],
    ],
    receiptTitle: "Draft PR #6141 was published",
    receiptText:
      "Improve OpenAPI upstream error details · linked to issue #6139",
    url: "https://github.com/PrefectHQ/fastmcp/pull/6141",
    caveat:
      "Recorded October 28, 2026. A maintainer later finished documentation and test details and merged this PR. That follow-through is separate from the workflow’s draft-publication result; the run did not merge its own change.",
  },
};

const flow = document.querySelector("#flow");
const detail = document.querySelector("#stage-detail");
let current = "stale";

function selectStage(index, reveal = false) {
  const entry = cases[current].steps[index];
  flow
    .querySelectorAll("button")
    .forEach((button, i) =>
      button.setAttribute("aria-pressed", String(i === index)),
    );
  detail.replaceChildren();
  const title = document.createElement("div");
  title.className = "detail-title";
  title.textContent = entry.name;
  const owner = document.createElement("span");
  owner.className = "detail-owner";
  owner.textContent = entry.owner;
  title.append(owner);
  const description = document.createElement("p");
  description.textContent = entry.detail;
  detail.append(title, description);
  if (reveal) detail.scrollIntoView({ block: "nearest" });
}

function selectCase(name) {
  current = name;
  const entry = cases[name];
  document
    .querySelectorAll("[data-scenario]")
    .forEach((button) =>
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.scenario === name),
      ),
    );
  document.querySelector("#case-title").textContent = entry.title;
  document.querySelector("#case-summary").textContent = entry.summary;
  const status = document.querySelector("#case-status");
  status.textContent = entry.status;
  status.className = `case-status ${entry.tone}`;
  flow.replaceChildren();
  flow.style.setProperty("--steps", entry.steps.length);
  entry.steps.forEach((step, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "flow-step";
    button.dataset.state = step.state;
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-controls", "stage-detail");
    button.setAttribute(
      "aria-label",
      `${index + 1}. ${step.name}: ${step.label}. Show details`,
    );
    for (const [className, text] of [
      ["step-number", String(index + 1).padStart(2, "0")],
      ["step-name", step.name],
      ["step-state", step.label],
    ]) {
      const span = document.createElement("span");
      span.className = className;
      span.textContent = text;
      button.append(span);
    }
    button.addEventListener("click", () => selectStage(index, true));
    flow.append(button);
  });
  const outcomes = document.querySelector("#outcomes");
  outcomes.replaceChildren();
  entry.facts.forEach(([label, value, tone]) => {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    definition.dataset.tone = tone;
    item.append(term, definition);
    outcomes.append(item);
  });
  const receipt = document.querySelector("#receipt");
  receipt.replaceChildren();
  receipt.className = entry.url ? "receipt" : "receipt unpublished";
  const symbol = document.createElement("span");
  symbol.className = "receipt-symbol";
  symbol.setAttribute("aria-hidden", "true");
  symbol.textContent = entry.url ? "✓" : "×";
  const body = document.createElement("div");
  const title = document.createElement("strong");
  if (entry.url) {
    const link = document.createElement("a");
    link.href = entry.url;
    link.textContent = entry.receiptTitle;
    title.append(link);
  } else title.textContent = entry.receiptTitle;
  const text = document.createElement("p");
  text.textContent = entry.receiptText;
  body.append(title, text);
  receipt.append(symbol, body);
  document.querySelector("#case-caveat").textContent = entry.caveat;
  selectStage(entry.selected);
}

document
  .querySelectorAll("[data-scenario]")
  .forEach((button) =>
    button.addEventListener("click", () => selectCase(button.dataset.scenario)),
  );
selectCase(current);
