import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const workflows = ["run-tests.yml", "run-static.yml"];
const marker = "<!-- marvin-ci-analysis:";
const bot = "marvin-context-protocol[bot]";

export function stopRequested(comments) {
  return comments.some(
    ({ user, body }) =>
      user.type !== "Bot" &&
      /(?:\b(?:marvin|bot)\b[^\n]{0,50}\b(?:stop|go away|don['’]?t comment|no more)\b|\b(?:stop|no more|don['’]?t)\b[^\n]{0,50}\b(?:marvin|bot comments?|commenting)\b)/i.test(
        body,
      ),
  );
}

// Resolve the target from GitHub, not the model. workflow_run.pull_requests is
// often empty for fork PRs; find open PRs by the source owner and branch.
export async function prepare(github, context) {
  const source = context.payload.workflow_run;
  if (source.event !== "pull_request")
    return { skip: "No PR target; inspect the source workflow's failed jobs." };
  const candidates = await github.paginate(github.rest.pulls.list, {
    ...context.repo,
    state: "open",
    head: `${source.head_repository.owner.login}:${source.head_branch}`,
    per_page: 100,
  });
  const matching = candidates.filter(
    (pr) =>
      pr.state === "open" &&
      pr.base.repo.full_name === `${context.repo.owner}/${context.repo.repo}` &&
      pr.head.sha === source.head_sha &&
      pr.head.repo?.full_name === source.head_repository.full_name,
  );
  if (matching.length !== 1)
    return { skip: "No unique open PR at this revision." };
  const { data: pr } = await github.rest.pulls.get({
    ...context.repo,
    pull_number: matching[0].number,
  });
  if (pr.state !== "open" || pr.head.sha !== source.head_sha)
    return { skip: "PR revision is obsolete." };

  const runs = [];
  for (const workflow_id of workflows) {
    const { data } = await github.rest.actions.listWorkflowRuns({
      ...context.repo,
      workflow_id,
      head_sha: source.head_sha,
      event: "pull_request",
      per_page: 100,
    });
    if (data.total_count > 100)
      return { skip: "Too many runs to establish the latest CI state." };
    const latest = data.workflow_runs
      .filter(
        (run) =>
          run.head_repository.full_name === source.head_repository.full_name,
      )
      .sort((a, b) => b.id - a.id)[0];
    if (latest) runs.push(latest);
  }
  // A completion event from the remaining sibling (even success) retries this
  // check. Both workflows trigger on every PR, so require both to be visible.
  if (
    runs.length !== workflows.length ||
    runs.some((run) => run.status !== "completed")
  )
    return { skip: "Waiting for both test and static workflows to finish." };
  const failed = runs.filter((run) => run.conclusion === "failure");
  if (!failed.length)
    return { skip: "No failed workflows at the current revision." };
  const fingerprint = `${pr.number}:${pr.head.sha}:${runs.map((run) => `${run.id}.${run.run_attempt}`).join(":")}`;
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...context.repo,
    issue_number: pr.number,
    per_page: 100,
  });
  if (stopRequested(comments))
    return { skip: "A participant asked Marvin to stop." };
  const existing = comments.find(
    (comment) => comment.user.login === bot && comment.body.startsWith(marker),
  );
  if (existing?.body.startsWith(`${marker}${fingerprint} -->`))
    return { skip: "This CI state is already analyzed." };

  const jobs = [];
  for (const run of failed) {
    const all = await github.paginate(
      github.rest.actions.listJobsForWorkflowRun,
      { ...context.repo, run_id: run.id, filter: "latest", per_page: 100 },
    );
    jobs.push(
      ...all
        .filter((job) => job.conclusion === "failure")
        .map((job) => ({
          id: job.id,
          name: job.name,
          html_url: job.html_url,
          run_id: run.id,
        })),
    );
  }
  if (!jobs.length)
    return {
      skip: "Workflow failed without a failed job; no test logs to diagnose.",
    };
  return { pr, jobs, runs, fingerprint, existing, comments };
}

// Prepare complete evidence on disk so the agent can choose what to inspect.
export async function collect(
  github,
  context,
  core,
  directory = `${process.env.RUNNER_TEMP}/marvin-ci`,
) {
  const plan = await prepare(github, context);
  if (plan.skip) {
    await core.summary
      .addHeading("Marvin CI analysis")
      .addRaw(plan.skip)
      .write();
    return;
  }
  mkdirSync(directory, { recursive: true });
  for (const job of plan.jobs) {
    const { data } = await github.rest.actions.downloadJobLogsForWorkflowRun({
      ...context.repo,
      job_id: job.id,
    });
    writeFileSync(
      `${directory}/job-${job.id}.log`,
      typeof data === "string" ? data : Buffer.from(data),
    );
  }
  const files = await github.paginate(github.rest.pulls.listFiles, {
    ...context.repo,
    pull_number: plan.pr.number,
    per_page: 100,
  });
  writeFileSync(
    `${directory}/evidence.json`,
    JSON.stringify({
      pr: {
        number: plan.pr.number,
        title: plan.pr.title,
        body: plan.pr.body,
        head: plan.pr.head.sha,
      },
      source_directory: `${process.env.GITHUB_WORKSPACE}/pr-source`,
      jobs: plan.jobs,
      files,
      discussion: plan.comments.map(({ user, body }) => ({
        author: user.login,
        body,
      })),
    }),
  );
  writeFileSync(
    `${directory}/analysis.json`,
    JSON.stringify({ fingerprint: plan.fingerprint }),
  );
  core.setOutput("ready", "true");
  core.setOutput("repository", plan.pr.head.repo.full_name);
  core.setOutput("sha", plan.pr.head.sha);
}

// Claude Code owns investigation and tool use. Only a successful final result
// can reach the publisher; turn/budget limits and denied tools fail visibly.
export async function acceptResult(
  core,
  directory = `${process.env.RUNNER_TEMP}/marvin-ci`,
) {
  const result = JSON.parse(readFileSync(`${directory}/result.json`, "utf8"));
  const text = result.result?.trim();
  if (
    result.subtype !== "success" ||
    result.is_error ||
    !text ||
    text.length > 8000 ||
    result.permission_denials?.length
  ) {
    throw new Error(
      "Investigation failed, hit a limit, or could not use its tools; no comment will be published.",
    );
  }
  core.info(
    `Investigation turns: ${result.num_turns}; reported cost: $${result.total_cost_usd}`,
  );
  if (text === "NO_ACTION") {
    await core.summary
      .addRaw("Marvin found no additional actionable diagnosis.")
      .write();
    return;
  }
  const path = `${directory}/analysis.json`;
  const analysis = JSON.parse(readFileSync(path, "utf8"));
  analysis.body = text.replaceAll("@", "@\u200b");
  writeFileSync(path, JSON.stringify(analysis));
  core.setOutput("publish", "true");
  await core.summary
    .addHeading("Marvin CI diagnosis")
    .addRaw(analysis.body)
    .write();
}

export async function publish(
  github,
  context,
  core,
  { input = `${process.env.RUNNER_TEMP}/marvin-ci/analysis.json` } = {},
) {
  const analysis = JSON.parse(readFileSync(input, "utf8"));
  // Recheck current SHA, latest run attempts, stop requests and prior comments
  // after inference. The app token can only publish the fixed PR comment.
  const plan = await prepare(github, context);
  if (plan.skip || plan.fingerprint !== analysis.fingerprint) {
    core.info(
      plan.skip ?? "CI changed during analysis; skipping stale diagnosis.",
    );
    return;
  }
  if (
    typeof analysis.body !== "string" ||
    !analysis.body.trim() ||
    analysis.body.length > 8000
  )
    throw new Error("Invalid diagnosis.");
  const sources = plan.jobs
    .map((job) => `[${job.name}](${job.html_url})`)
    .join(" · ");
  const body = `${marker}${plan.fingerprint} -->\n${analysis.body}\n\n<details><summary>CI evidence</summary>\n\nRevision: ${plan.pr.head.sha}\n\n${sources}\n\n</details>`;
  if (plan.existing) {
    await github.rest.issues.updateComment({
      ...context.repo,
      comment_id: plan.existing.id,
      body,
    });
  } else {
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: plan.pr.number,
      body,
    });
  }
  core.info(`Published CI diagnosis for PR #${plan.pr.number}`);
}
