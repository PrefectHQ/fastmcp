import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import {
  collect,
  acceptResult,
  prepare,
  publish,
  stopRequested,
} from "./analyze-ci-failure.mjs";

function fixture() {
  const pr = {
    number: 42,
    state: "open",
    title: "Fix client",
    head: { sha: "head", repo: { full_name: "contributor/fastmcp" } },
    base: { repo: { full_name: "PrefectHQ/fastmcp" } },
  };
  const context = {
    repo: { owner: "PrefectHQ", repo: "fastmcp" },
    payload: {
      workflow_run: {
        event: "pull_request",
        head_sha: "head",
        head_repository: {
          full_name: "contributor/fastmcp",
          owner: { login: "contributor" },
        },
        head_branch: "fix-client",
        pull_requests: [],
      },
    },
  };
  const runs = [
    {
      id: 10,
      run_attempt: 1,
      status: "completed",
      conclusion: "failure",
      head_repository: { full_name: "contributor/fastmcp" },
    },
    {
      id: 11,
      run_attempt: 1,
      status: "completed",
      conclusion: "success",
      head_repository: { full_name: "contributor/fastmcp" },
    },
  ];
  const jobs = [
    {
      id: 100,
      name: "Unit tests",
      conclusion: "failure",
      html_url: "https://github.com/PrefectHQ/fastmcp/actions/runs/10/job/100",
    },
  ];
  const comments = [];
  const writes = [];
  const summaries = [];
  const outputs = [];
  const core = {
    info() {},
    setOutput: (...args) => outputs.push(args),
    summary: {
      addHeading() {
        return this;
      },
      addRaw(text) {
        summaries.push(text);
        return this;
      },
      async write() {},
    },
  };
  const github = {
    rest: {
      pulls: {
        list: "prs",
        get: async () => ({ data: pr }),
        listFiles: "files",
      },
      issues: {
        listComments: "comments",
        createComment: async (args) => writes.push({ type: "create", ...args }),
        updateComment: async (args) => writes.push({ type: "update", ...args }),
      },
      actions: {
        listWorkflowRuns: async ({ workflow_id }) => ({
          data: {
            total_count: 1,
            workflow_runs: [runs[workflow_id === "run-tests.yml" ? 0 : 1]],
          },
        }),
        listJobsForWorkflowRun: "jobs",
        downloadJobLogsForWorkflowRun: async () => ({
          data: "FAILED test_client: assertion mismatch",
        }),
      },
    },
    paginate: async (route) =>
      ({
        prs: [pr],
        jobs,
        comments,
        files: [{ filename: "client.py", patch: "-old\n+new" }],
      })[route],
  };
  return {
    github,
    context,
    core,
    pr,
    runs,
    jobs,
    comments,
    writes,
    summaries,
    outputs,
  };
}

async function withOutput(fn) {
  const dir = mkdtempSync(join(tmpdir(), "marvin-ci-test-"));
  try {
    await fn(join(dir, "analysis.json"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("fork PR resolution does not depend on workflow_run.pull_requests", async () => {
  const f = fixture();
  const plan = await prepare(f.github, f.context);
  assert.equal(plan.pr.number, 42);
  assert.equal(plan.jobs.length, 1);
  assert.equal(plan.fingerprint, "42:head:10.1:11.1");
});
for (const scenario of [
  "closed",
  "obsolete",
  "wrong-repo",
  "pending",
  "success",
  "no-jobs",
  "main",
]) {
  test(`skip without inference: ${scenario}`, async () => {
    const f = fixture();
    if (scenario === "closed") f.pr.state = "closed";
    if (scenario === "obsolete") f.pr.head.sha = "new-head";
    if (scenario === "wrong-repo") f.pr.head.repo.full_name = "another/fastmcp";
    if (scenario === "pending") f.runs[1].status = "in_progress";
    if (scenario === "success") f.runs[0].conclusion = "success";
    if (scenario === "no-jobs") f.jobs.length = 0;
    if (scenario === "main") f.context.payload.workflow_run.event = "push";
    await collect(f.github, f.context, f.core);
    assert.equal(f.outputs.length, 0);
    assert.equal(f.summaries.length, 1);
  });
}
test("successful sibling completion diagnoses an earlier failure", async () => {
  const f = fixture();
  f.context.payload.workflow_run.conclusion = "success";
  assert.equal((await prepare(f.github, f.context)).jobs.length, 1);
});
test("already-published CI state is not analyzed twice", async () => {
  const f = fixture();
  f.comments.push({
    user: { type: "Bot", login: "marvin-context-protocol[bot]" },
    body: "<!-- marvin-ci-analysis:42:head:10.1:11.1 -->\nDiagnosis",
  });
  assert.match((await prepare(f.github, f.context)).skip, /already analyzed/);
  f.runs[0].run_attempt = 2;
  assert.equal(
    (await prepare(f.github, f.context)).fingerprint,
    "42:head:10.2:11.1",
  );
});
test("human stop requests suppress diagnosis", async () => {
  for (const body of [
    "Marvin, stop commenting",
    "No more bot comments",
    "Don't comment anymore, Marvin",
    "bot, go away",
  ]) {
    assert.equal(stopRequested([{ user: { type: "User" }, body }]), true);
  }
  assert.equal(
    stopRequested([
      { user: { type: "User" }, body: "The server stops during shutdown" },
    ]),
    false,
  );
  const f = fixture();
  f.comments.push({ user: { type: "User" }, body: "Marvin, stop" });
  assert.match((await prepare(f.github, f.context)).skip, /stop/);
});
test("agent gets complete logs and context, with publication separate", async () => {
  await withOutput(async (output) => {
    const f = fixture();
    const directory = dirname(output);
    const log = "earlier failure\n" + "context\n".repeat(2000);
    f.github.rest.actions.downloadJobLogsForWorkflowRun = async () => ({
      data: log,
    });
    await collect(f.github, f.context, f.core, directory);
    assert.equal(readFileSync(join(directory, "job-100.log"), "utf8"), log);
    const evidence = JSON.parse(
      readFileSync(join(directory, "evidence.json"), "utf8"),
    );
    assert.equal(evidence.pr.head, "head");
    assert.deepEqual(evidence.files, [
      { filename: "client.py", patch: "-old\n+new" },
    ]);
    assert.deepEqual(f.outputs, [
      ["ready", "true"],
      ["repository", "contributor/fastmcp"],
      ["sha", "head"],
    ]);
    writeFileSync(
      join(directory, "result.json"),
      JSON.stringify({
        subtype: "success",
        is_error: false,
        result: "Fix the assertion @someone.",
        num_turns: 4,
        total_cost_usd: 0.15,
      }),
    );
    await acceptResult(f.core, directory);
    assert.equal(f.writes.length, 0);
    assert.deepEqual(f.outputs.at(-1), ["publish", "true"]);
    await publish(f.github, f.context, f.core, {
      input: join(directory, "analysis.json"),
    });
    assert.equal(f.writes[0].issue_number, 42);
    assert.match(f.writes[0].body, /@\u200bsomeone/);
    assert.match(f.writes[0].body, /actions\/runs\/10\/job\/100/);
  });
});
for (const scenario of ["obsolete", "rerun", "stop", "already-posted"]) {
  test(`publisher rejects stale output: ${scenario}`, async () => {
    await withOutput(async (input) => {
      const f = fixture();
      const plan = await prepare(f.github, f.context);
      writeFileSync(
        input,
        JSON.stringify({ fingerprint: plan.fingerprint, body: "Diagnosis" }),
      );
      if (scenario === "obsolete") f.pr.head.sha = "new-head";
      if (scenario === "rerun") f.runs[0].run_attempt = 2;
      if (scenario === "stop")
        f.comments.push({ user: { type: "User" }, body: "Marvin, stop" });
      if (scenario === "already-posted")
        f.comments.push({
          user: { type: "Bot", login: "marvin-context-protocol[bot]" },
          body: `<!-- marvin-ci-analysis:${plan.fingerprint} -->\nDiagnosis`,
        });
      await publish(f.github, f.context, f.core, { input });
      assert.equal(f.writes.length, 0);
    });
  });
}
test("publisher updates only Marvin's marked comment", async () => {
  await withOutput(async (input) => {
    const f = fixture();
    f.comments.push({
      id: 1,
      user: { type: "User", login: "someone" },
      body: "<!-- marvin-ci-analysis:old -->",
    });
    f.comments.push({
      id: 2,
      user: { type: "Bot", login: "marvin-context-protocol[bot]" },
      body: "<!-- marvin-ci-analysis:old -->",
    });
    const plan = await prepare(f.github, f.context);
    writeFileSync(
      input,
      JSON.stringify({ fingerprint: plan.fingerprint, body: "New diagnosis" }),
    );
    await publish(f.github, f.context, f.core, { input });
    assert.equal(f.writes[0].type, "update");
    assert.equal(f.writes[0].comment_id, 2);
  });
});
test("failed, denied, empty and over-budget investigations cannot publish", async () => {
  for (const result of [
    { subtype: "error_max_turns", result: "Incomplete" },
    { subtype: "error_max_budget_usd", result: "Incomplete" },
    { subtype: "success", is_error: true, result: "Error" },
    { subtype: "success", result: "" },
    { subtype: "success", result: "x".repeat(8001) },
    {
      subtype: "success",
      result: "Diagnosis",
      permission_denials: [{ tool_name: "Read" }],
    },
  ]) {
    await withOutput(async (output) => {
      const f = fixture();
      writeFileSync(
        join(dirname(output), "result.json"),
        JSON.stringify(result),
      );
      await assert.rejects(acceptResult(f.core, dirname(output)));
      assert.equal(f.outputs.length, 0);
      assert.equal(f.writes.length, 0);
    });
  }
});

test("NO_ACTION produces no publication output", async () => {
  await withOutput(async (output) => {
    const f = fixture();
    writeFileSync(
      join(dirname(output), "result.json"),
      JSON.stringify({ subtype: "success", result: "NO_ACTION" }),
    );
    await acceptResult(f.core, dirname(output));
    assert.equal(f.outputs.length, 0);
    assert.equal(f.writes.length, 0);
  });
});
