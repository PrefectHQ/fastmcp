import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

// Execute the workflow's actual scripts against a fake GitHub API. Run after
// uv sync: node --test .github/scripts/test-issue-link.mjs
const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const scripts = JSON.parse(
  execFileSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      "-c",
      `
import json
from pathlib import Path
import yaml
workflow = yaml.safe_load(Path('.github/workflows/require-issue-link.yml').read_text())
print(json.dumps({name: job['steps'][0]['with']['script'] for name, job in workflow['jobs'].items()}))
`,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  ),
);
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
async function run(job, options = {}) {
  const calls = [];
  const failures = [];
  const pr = {
    number: 100,
    user: { login: "contributor" },
    body: options.body ?? "Fixes #42",
    state: options.state ?? "open",
    labels: options.gated ? [{ name: "missing-issue-link" }] : [],
    head: { sha: "head" },
  };
  const context = {
    repo: { owner: "PrefectHQ", repo: "fastmcp" },
    payload: {
      pull_request: pr,
      action: "opened",
      sender: { login: "contributor" },
      issue: { number: 42 },
      assignee: { login: "contributor" },
    },
  };
  const record = (name) => async (args) => {
    calls.push({ name, args });
    return { data: {} };
  };
  const github = {
    rest: {
      repos: {
        getCollaboratorPermissionLevel: async () => ({
          data: { permission: options.maintainer ? "write" : "read" },
        }),
      },
      issues: {
        getLabel: async () => ({ data: {} }),
        addLabels: record("addLabels"),
        removeLabel: record("removeLabel"),
        listLabelsOnIssue: async () => ({ data: [] }),
        get: async ({ issue_number }) => ({
          data:
            issue_number === 42
              ? {
                  assignees: options.assigned ? [{ login: "contributor" }] : [],
                  labels: options.welcome ? [{ name: "prs welcome" }] : [],
                }
              : { ...pr, labels: [{ name: "missing-issue-link" }] },
        }),
        listEvents: async () => options.events ?? [],
        listComments: async () => [],
        createComment: record("createComment"),
        updateComment: record("updateComment"),
      },
      pulls: { update: record("updatePR"), get: async () => ({ data: pr }) },
      search: {
        issuesAndPullRequests: async ({ q }) => {
          calls.push({ name: "search", args: q });
          const excluded = q.includes("is:closed") && pr.state === "open";
          return {
            data: {
              total_count: excluded ? 0 : 1,
              items: excluded ? [] : [{ number: 100 }],
            },
          };
        },
      },
      actions: {
        listWorkflowRuns: async () => ({
          data: { workflow_runs: [{ id: 7 }] },
        }),
        reRunWorkflowFailedJobs: record("rerun"),
      },
    },
    paginate: async (method, args) => method(args),
    graphql: record("graphql"),
  };
  const core = {
    setFailed: (message) => failures.push(message),
    warning: () => {},
  };
  process.env.ENFORCE_ISSUE_LINK = options.dryRun ? "false" : "true";
  await new AsyncFunction("github", "context", "core", "console", scripts[job])(
    github,
    context,
    core,
    { log() {} },
  );
  return { calls, failures };
}
const check = "check-issue-link";
const assign = "reopen-on-assignment";
test("linked PR waits for assignment without closing", async () => {
  const result = await run(check);
  assert.equal(result.failures.length, 1);
  assert(!result.calls.some((c) => c.name === "updatePR"));
  assert(
    result.calls
      .find((c) => c.name === "createComment")
      .args.body.includes("awaiting maintainer assignment"),
  );
});

test("missing issue link closes the PR", async () => {
  const result = await run(check, { body: "No link" });
  assert.equal(result.failures.length, 1);
  assert.equal(
    result.calls.find((c) => c.name === "updatePR").args.state,
    "closed",
  );
});

for (const options of [
  { assigned: true },
  { welcome: true },
  { maintainer: true },
]) {
  test(`eligible PR clears enforcement: ${JSON.stringify(options)}`, async () => {
    const result = await run(check, options);
    assert.equal(result.failures.length, 0);
    assert(result.calls.some((c) => c.name === "removeLabel"));
    assert(!result.calls.some((c) => c.name === "updatePR"));
  });
}

test("assignment rechecks an open PR without reopening it", async () => {
  const result = await run(assign);
  assert(result.calls.some((c) => c.name === "rerun"));
  assert(result.calls.some((c) => c.name === "removeLabel"));
  assert(!result.calls.some((c) => c.name === "updatePR"));
});

test("assignment reopens a closed PR and reruns the check", async () => {
  const result = await run(assign, { state: "closed" });
  assert.equal(
    result.calls.find((c) => c.name === "updatePR").args.state,
    "open",
  );
  assert(result.calls.some((c) => c.name === "rerun"));
});

test("assignment does not change PRs for another issue", async () => {
  const result = await run(assign, { body: "Fixes #99" });
  assert(
    !result.calls.some((c) =>
      ["updatePR", "rerun", "removeLabel"].includes(c.name),
    ),
  );
});

const botClosure = { event: "closed", actor: { login: "github-actions[bot]" } };

test("adding a link reopens a gate-closed PR but keeps assignment required", async () => {
  const result = await run(check, {
    state: "closed",
    gated: true,
    events: [botClosure],
  });
  assert.equal(
    result.calls.find((c) => c.name === "updatePR")?.args.state,
    "open",
  );
  assert.equal(result.failures.length, 1);
  assert(!result.calls.some((c) => c.name === "removeLabel"));
});

test("a later maintainer closure takes precedence over an earlier gate closure", async () => {
  const result = await run(check, {
    state: "closed",
    gated: true,
    events: [botClosure, { event: "closed", actor: { login: "maintainer" } }],
  });
  assert(!result.calls.some((c) => c.name === "updatePR"));
  assert.equal(result.failures.length, 1);
});

test("a closed PR without the gate label is not reopened", async () => {
  const result = await run(check, { state: "closed", events: [botClosure] });
  assert(!result.calls.some((c) => c.name === "updatePR"));
});

for (const job of [check, assign]) {
  test(`dry run does not mutate GitHub: ${job}`, async () => {
    const result = await run(job, {
      dryRun: true,
      state: "closed",
      gated: true,
      events: [botClosure],
    });
    assert.equal(result.failures.length, 0);
    assert(!result.calls.some((c) => c.name !== "search"));
  });
}
