import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  diagnose,
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
    let calls = 0;
    await diagnose(f.github, f.context, f.core, {
      fetcher: async () => {
        calls++;
        throw new Error("unexpected inference");
      },
    });
    assert.equal(calls, 0);
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
test("one bounded model request yields text only, never GitHub writes", async () => {
  await withOutput(async (output) => {
    const f = fixture();
    let calls = 0;
    await diagnose(f.github, f.context, f.core, {
      output,
      key: "test-key",
      fetcher: async (url, request) => {
        calls++;
        assert.equal(url, "https://api.anthropic.com/v1/messages");
        const body = JSON.parse(request.body);
        assert.equal(body.model, "claude-sonnet-5");
        assert.equal(body.max_tokens, 1200);
        assert.equal(body.tools, undefined);
        assert.match(body.messages[0].content, /FAILED test_client/);
        return {
          ok: true,
          json: async () => ({
            stop_reason: "end_turn",
            content: [{ type: "text", text: "Fix the assertion @someone." }],
            usage: { input_tokens: 100, output_tokens: 10 },
          }),
        };
      },
    });
    assert.equal(calls, 1);
    assert.equal(f.writes.length, 0);
    assert.deepEqual(f.outputs, [["publish", "true"]]);
    await publish(f.github, f.context, f.core, { input: output });
    assert.equal(f.writes.length, 1);
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
test("truncated inference and API errors cannot publish", async () => {
  for (const response of [
    { ok: false, status: 429 },
    {
      ok: true,
      json: async () => ({
        stop_reason: "max_tokens",
        content: [{ type: "text", text: "Incomplete" }],
      }),
    },
  ]) {
    const f = fixture();
    await assert.rejects(
      diagnose(f.github, f.context, f.core, { fetcher: async () => response }),
    );
    assert.equal(f.outputs.length, 0);
    assert.equal(f.writes.length, 0);
  }
});

test("NO_ACTION produces no publication output", async () => {
  const f = fixture();
  await diagnose(f.github, f.context, f.core, {
    fetcher: async () => ({
      ok: true,
      json: async () => ({
        stop_reason: "end_turn",
        content: [{ type: "text", text: "NO_ACTION" }],
      }),
    }),
  });
  assert.equal(f.outputs.length, 0);
  assert.equal(f.writes.length, 0);
});
