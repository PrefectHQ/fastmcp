# Manual server-authoring evaluations

These fixtures exercise the `fastmcp-server` skill through fresh Pi sessions. They are development experiments, not CI tests or evidence of a general model-quality improvement. Generated servers and transcripts belong outside the repository.

## Run a trial

Install Pi separately and authenticate the `openai-codex` provider. `pi auth check --provider openai-codex --json` reports readiness without printing credentials. Prepare separate Python environments for the two supported task versions:

```bash
uv venv /tmp/fastmcp-eval-v4
uv --no-config pip install --python /tmp/fastmcp-eval-v4/bin/python 'fastmcp==4.1.3'
uv venv /tmp/fastmcp-eval-v3
uv --no-config pip install --python /tmp/fastmcp-eval-v3/bin/python 'fastmcp==3.4.11'
```

From the repository root, run the same case twice with different, nonexistent output directories. The only intended difference is availability of the skill:

```bash
uv run python dev-docs/skill-evals/run.py composition \
  --python /tmp/fastmcp-eval-v3/bin/python \
  --output /tmp/portal-baseline
uv run python dev-docs/skill-evals/run.py composition \
  --python /tmp/fastmcp-eval-v3/bin/python \
  --output /tmp/portal-skill \
  --skill skills/fastmcp-server/SKILL.md
```

Use `catalog` or `inventory` with the v4 environment. The default model is `gpt-6-luna`; `--model gpt-6.1-sol` selects Sol. Thinking is medium. Each authoring run is capped at eight minutes. Existing output directories are rejected. The runner copies fixtures into a fresh workspace, snapshots the skill, and retains its SHA256, prompt, transcript, elapsed time, server output, and independent checks. Timeouts, nonzero authoring exits, and failed behavioral checks cause a nonzero runner exit. A normal model exit alone does not indicate success.

Pi runs with global extensions, skills, prompt templates, context files, and themes disabled. Only an explicit skill is available, and the user prompt does not explicitly invoke it. Check the transcript to distinguish skill availability from actual reading. Sessions are ephemeral. These flags isolate agent context; they do not provide an operating-system sandbox. Task prompts restrict work to the trial workspace, installed packages, public documentation, and the explicitly available skill. API-key environment variables are not forwarded; Pi uses its existing provider authentication.

## What is checked

- `catalog`: one-page search results, continuation and completeness, finite categories, upstream errors, and a prompt. Per-property descriptions are recorded separately as a schema diagnostic; descriptions in the tool text also satisfy the task.
- `inventory`: no database at import, real SQLite state, structured transfer results, six rejected transfers without partial writes, resources, prompts, and preservation across restarts. Connection closing is inspected in generated source, not proved by a protocol assertion.
- `composition`: pinned FastMCP 3.4.11, live namespaced mounts, independently usable children, post-start registration, tool collisions, resource templates, prompts, and masked internal faults alongside actionable expected errors.

Acceptance scripts use the installed FastMCP `Client` and real local data. They are outside the model workspace. Each original acceptance file was written before inspecting its corresponding generated servers. A check that crashes records an unhandled failure; do not compare only the reduced denominator after an early crash.

`check_prompt_links.py` was added after tracing a failure missed by the original composition assertions. It collects prompt text, discovered templates, and reachability of literal article URIs. Its status is **unassessed** until a person interprets the prompt. An unreachable URI may be a correctly labeled alternative for a different server context. A prompt may validly instruct discovery without containing a literal URI. The scanner does not grade English instructions and does not fail the run merely for an unreachable candidate. Manually follow the instruction through both the portal and standalone docs server; verify that the selected resource returns the expected article.

Inspect transcripts as well as scores: whether the skill was read, relevant documentation was retrieved, client-visible schemas were examined, error messages retained useful information, and the agent's own tests reached the actual protocol. Token totals below are summed Pi usage across turns, including cached tokens, not peak context size or a dollar-cost estimate.

## Results from October 20, 2026

Environment: Pi 0.87.0, Python 3.12.13, models `openai-codex/gpt-6-luna` and `openai-codex/gpt-6.1-sol`, thinking medium. The task environments used FastMCP 4.1.3 and 3.4.11. Sixteen authoring runs included initial pairs, revised-skill pairs, one Sol pair, and final skill-only smoke runs. This is a small, iterative convenience sample: it does not establish efficacy, statistical significance, or an expected cost change.

The catalog and inventory behavioral checks passed in every run. In the first Luna composition pair, baseline code raised `ValueError` for missing articles; masking discarded its recovery message. The skill variant preserved that message and passed all original checks. A later baseline passed those checks too. Both initial Luna composition variants nevertheless emitted a standalone URI inside the mounted prompt, which the portal could not read. This revealed a gap in the original acceptance checks. Sol handled the portal/standalone distinction in both variants; the first literal-URI scanner produced false positives for its correctly labeled alternatives, which were rejected after manual review.

The v3 skill-available Luna run did not read the skill and still emitted the bad mounted URI. That is an activation failure, not evidence about following that revision's new instruction. Documentation retrieval was inconsistent even when the skill was read. The v4 skill adds the causal fact that namespacing changes component names and URIs without rewriting URI text inside prompt messages.

The v4 Luna run read the skill and returned a discovery-based prompt. Following it through each actual client succeeded: the portal advertised `article://docs/{slug}` and returned the expected article at `article://docs/install`; the standalone server advertised `article://{slug}` and returned the same article at `article://install`. However, that v4 run again masked expected missing-article errors, failing two original checks (14/16). The portable runner exited nonzero. This failure is retained, not presented as a successful end-to-end server.

Only the v2 catalog Luna run and v2 composition Sol run fetched public documentation. The v3 skill-available Luna run never read the skill. The final smoke results support the specific guidance but do not demonstrate an overall reliability improvement. The skill remains advisory, and independent protocol and workflow checks remain necessary.

The first four rows used v1; the next eight used v2 when enabled. The v3 rows are a fresh pair. The v4 and v5 rows are iterative skill-only smoke runs, not independent paired comparisons. All runs except those explicitly named `sol` used Luna. Composition URI behavior is reported in the prose above rather than folded into the original 16-check score.

| Run | Behavioral checks | Skill read | Seconds | Tool calls | Total tokens |
| --- | --- | --- | ---: | ---: | ---: |
| catalog-baseline | 9/9 | no | 29.14 | 8 | 16,584 |
| catalog-skill | 9/9 | yes | 34.35 | 8 | 26,390 |
| inventory-baseline | 16/16 | no | 40.55 | 9 | 29,015 |
| inventory-skill | 16/16 | yes | 40.18 | 8 | 36,229 |
| catalog-baseline-v2 | 9/9 | no | 20.12 | 6 | 14,787 |
| catalog-skill-v2 | 9/9 | yes | 23.39 | 10 | 45,154 |
| inventory-baseline-v2 | 16/16 | no | 37.79 | 11 | 61,279 |
| inventory-skill-v2 | 16/16 | yes | 49.54 | 12 | 191,872 |
| composition-baseline-luna | 14/16 | no | 34.91 | 13 | 56,186 |
| composition-skill-luna | 16/16 | yes | 34.29 | 13 | 44,740 |
| composition-baseline-sol | 16/16 | no | 105.51 | 17 | 93,200 |
| composition-skill-sol | 16/16 | yes | 114.36 | 16 | 461,565 |
| composition-baseline-v3 | 16/16 | no | 53.35 | 16 | 68,689 |
| composition-skill-v3 | 16/16 | no | 27.43 | 10 | 40,837 |
| composition-skill-v4 | 14/16 | yes | 47.75 | 15 | 70,650 |
| composition-skill-v5 | 16/16 | yes | 34.21 | 13 | 62,743 |

The final v5 skill explicitly names FastMCP’s expected-error exception classes and explains masking. Its Luna smoke run read the skill and passed all 16 original checks. The prompt used template discovery; manual protocol follow-through again read the expected article through both `article://docs/install` on the portal and `article://install` on the standalone server. This was targeted iteration on observed failures, not a new efficacy experiment.

Skill snapshots, SHA256:

- v1: `15f5e80f6525b327f06231af1eb6abcc3dd63c90a3269aa09bea0aa40a0e859c`
- v2: `976208b38f892a89c8149b79f06a86bc103049875d45a436bb63f9d4f380e939`
- v3: `8bfea437092a5c81c58dfda2d5c8aa9fb2020e71d533598794bf9e8c7c2bbdc8`
- v4: `bf78ace0ae20688f04ceef08a4418fa4501b2a79e6c554f75393e97ff1945dcc`

- v5, final: `9f5b13ee1a020c42d986328ab29a61a138bbf757a393739882dd01e11a042c69`

Initial fixtures and unmodified transcripts are retained in the task's external evaluation artifacts. They are intentionally not committed. The portable runner was exercised on the final composition iterations. Repeated runs can differ; retain unsuccessful runs and disclose whether checks or skill text were added after observing a failure.
