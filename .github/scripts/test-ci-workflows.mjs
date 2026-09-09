import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const workflows = JSON.parse(
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
# BaseLoader preserves the GitHub Actions 'on' key rather than YAML 1.1's boolean.
print(json.dumps({name: yaml.load(Path(f'.github/workflows/{name}.yml').read_text(), Loader=yaml.BaseLoader)
                  for name in ['run-tests', 'run-static', 'run-upgrade-checks']}))
`,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  ),
);
const workflow = workflows["run-tests"];
const script = workflow.jobs.changes.steps[0].with.script;
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function classify(
  files,
  {
    event = "pull_request",
    count = files.length,
    error = false,
    moved = false,
  } = {},
) {
  const warnings = [];
  let calls = 0;
  const result = await new AsyncFunction("github", "context", "core", script)(
    {
      rest: {
        pulls: {
          listFiles: "listFiles",
          get: async () => ({
            data: {
              head: { sha: moved ? "new-head" : "head" },
              base: { sha: "base" },
            },
          }),
        },
      },
      paginate: async (route, args) => {
        calls++;
        assert.equal(route, "listFiles");
        assert.deepEqual(args, {
          owner: "PrefectHQ",
          repo: "fastmcp",
          pull_number: 42,
          per_page: 100,
        });
        if (error) throw new Error("API unavailable");
        return files;
      },
    },
    {
      eventName: event,
      repo: { owner: "PrefectHQ", repo: "fastmcp" },
      payload: {
        pull_request: {
          number: 42,
          changed_files: count,
          head: { sha: "head" },
          base: { sha: "base" },
        },
      },
    },
    { warning: (message) => warnings.push(message) },
  );
  return { result, calls, warnings };
}

for (const filename of [
  "README.md",
  "CONTRIBUTING.md",
  "CODE_OF_CONDUCT.md",
  "docs/clients/tools.mdx",
  "docs/v3/guide.md",
]) {
  test(`editorial change: ${filename}`, async () => {
    assert.equal((await classify([{ filename }])).result, false);
  });
}
for (const filename of [
  "fastmcp_slim/fastmcp/server.py",
  "fastmcp_remote/cli.py",
  "fastmcp_tasks/extension.py",
  "tests/test_client.py",
  "examples/demo.py",
  ".github/actions/setup-uv/action.yml",
  ".github/workflows/run-tests.yml",
  ".github/scripts/test-ci-workflows.mjs",
  "pyproject.toml",
  "uv.lock",
  ".pre-commit-config.yaml",
  "docs/script.js",
  "docs/snippets/example.py",
  "new-package/README.md",
  "CLAUDE.md",
]) {
  test(`full coverage with mixed changes: ${filename}`, async () => {
    assert.equal(
      (await classify([{ filename: "README.md" }, { filename }])).result,
      true,
    );
  });
}
test("renaming code to documentation still runs tests", async () => {
  assert.equal(
    (
      await classify([
        { filename: "docs/old.md", previous_filename: "fastmcp_slim/old.py" },
      ])
    ).result,
    true,
  );
});
test("renaming documentation to code still runs tests", async () => {
  assert.equal(
    (await classify([{ filename: "new.py", previous_filename: "docs/old.md" }]))
      .result,
    true,
  );
});
test("editorial rename can skip tests", async () => {
  assert.equal(
    (
      await classify([
        { filename: "docs/new.md", previous_filename: "docs/old.md" },
      ])
    ).result,
    false,
  );
});
test("complete paginated editorial diff can skip tests", async () => {
  assert.equal(
    (
      await classify(
        Array.from({ length: 150 }, (_, i) => ({ filename: `docs/${i}.md` })),
      )
    ).result,
    false,
  );
});
test("empty, incomplete, changed and capped lists run tests", async () => {
  for (const count of [0, 2, 3001]) {
    assert.equal(
      (await classify([{ filename: "README.md" }], { count })).result,
      true,
    );
  }
  assert.equal((await classify([])).result, true);
  assert.equal(
    (
      await classify(
        Array.from({ length: 3000 }, () => ({ filename: "README.md" })),
      )
    ).result,
    true,
  );
});
test("API failure falls back to full coverage", async () => {
  const result = await classify([], { error: true });
  assert.equal(result.result, true);
  assert.equal(result.warnings.length, 1);
});
for (const event of ["push", "workflow_dispatch"]) {
  test(`${event} always runs tests without consulting PR files`, async () => {
    const result = await classify([], { event });
    assert.equal(result.result, true);
    assert.equal(result.calls, 0);
  });
}
test("required matrix checks retain names even when editorial steps skip", () => {
  const matrix = workflow.jobs.run_tests;
  assert.equal(
    matrix.name,
    "Tests: Python ${{ matrix.python-version }} on ${{ matrix.os }}",
  );
  assert.deepEqual(matrix.strategy.matrix, {
    os: ["ubuntu-latest", "windows-latest"],
    "python-version": ["3.10"],
    include: [{ os: "ubuntu-latest", "python-version": "3.13" }],
  });
  assert.equal(matrix.if, "${{ !cancelled() }}");
  assert.equal(matrix.steps[0].uses, "actions/checkout@v7");
  for (const step of matrix.steps.slice(2, 4))
    assert.equal(step.if, "needs.changes.outputs.run-tests != 'false'");
  assert.equal(
    matrix.steps[1].if,
    "needs.changes.outputs.run-tests != 'false' || (matrix.os == 'ubuntu-latest' && matrix.python-version == '3.10')",
  );
  assert.equal(
    matrix.steps[4].if,
    "needs.changes.outputs.run-tests == 'false' && matrix.os == 'ubuntu-latest' && matrix.python-version == '3.10'",
  );
  assert.equal(matrix.steps[4].run, "uv run pytest tests/docs -n 0");
  for (const name of [
    "run_tests_lowest_direct",
    "run_conformance_tests",
    "run_integration_tests",
    "package_install_smoke",
  ]) {
    assert.equal(workflow.jobs[name].needs, "changes");
    assert.equal(
      workflow.jobs[name].if,
      "${{ !cancelled() && needs.changes.outputs.run-tests != 'false' }}",
    );
  }
});
test("PR cancellation cannot supersede main or manual runs", () => {
  for (const name of ["run-tests", "run-static"]) {
    assert.deepEqual(workflows[name].concurrency, {
      group:
        "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}",
      "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    });
    assert.deepEqual(workflows[name].on.push, { branches: ["main"] });
    assert.ok(Object.hasOwn(workflows[name].on, "pull_request"));
    assert.ok(Object.hasOwn(workflows[name].on, "workflow_dispatch"));
  }
  assert.equal(workflows["run-static"].jobs.static_analysis.if, undefined);
});
test("upgrade coverage remains nightly and manually dispatchable", () => {
  assert.deepEqual(workflows["run-upgrade-checks"].on, {
    schedule: [{ cron: "0 2 * * *" }],
    workflow_dispatch: "",
  });
  assert.ok(workflows["run-upgrade-checks"].jobs.notify);
  assert.ok(workflows["run-upgrade-checks"].jobs["close-on-success"]);
});

test("a PR updated during classification gets full coverage", async () => {
  assert.equal(
    (await classify([{ filename: "README.md" }], { moved: true })).result,
    true,
  );
});
