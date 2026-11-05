export function allowed(payload, allowlist, actor) {
  return (
    payload.action === "created" &&
    Boolean(payload.issue?.pull_request) &&
    payload.comment?.body?.trim() === "/roast" &&
    payload.comment?.user?.type === "User" &&
    actor === payload.comment.user.login &&
    Array.isArray(allowlist) &&
    allowlist.includes(actor)
  );
}

export async function prepare({ github, context, core, allowlist }) {
  if (!allowed(context.payload, allowlist, context.actor)) return;
  const { data: pr } = await github.rest.pulls.get({
    ...context.repo,
    pull_number: context.issue.number,
  });
  if (pr.state !== "open") return;
  for (const sha of [pr.head.sha, pr.base.sha]) {
    if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error("Invalid PR revision");
  }
  core.setOutput("head", pr.head.sha);
  core.setOutput("base", pr.base.sha);
  core.setOutput("allowed", "true");
}

export function reportBody(report, { head, base, url, exitCode, stale }) {
  const valid =
    report &&
    Array.isArray(report.findings) &&
    report.quick === false &&
    report.verified === true &&
    ["NO_FINDINGS", "FINDINGS_PRESENT"].includes(report.assessment);
  const complete =
    valid &&
    exitCode === 0 &&
    !report.incomplete &&
    !report.diff_truncated &&
    report.findings.every(
      (finding) =>
        finding.verdict?.status === "confirmed" && !finding.verdict.undecided,
    );
  const status = complete
    ? `${report.findings.length} verified finding(s).`
    : "Review incomplete or failed; this is not a clean review.";
  const detail = valid
    ? JSON.stringify(
        {
          findings: report.findings,
          notes: report.notes,
          usage: report.usage,
          incomplete: report.incomplete,
          diff_truncated: report.diff_truncated,
        },
        null,
        2,
      )
    : "No valid verified report was produced. See the workflow logs.";
  const escaped = detail.replaceAll("`", "\\u0060");
  const clipped =
    escaped.length > 45000
      ? `${escaped.slice(0, 45000)}\n[Truncated; download report.json from the run.]`
      : escaped;
  const fence = "```";
  return (
    `Roast: ${status}\n\nReviewed ${head} against base ${base}.` +
    (stale
      ? " **The PR changed during review; this report is outdated.**"
      : "") +
    `\n\n[Run and report artifact](${url})\n\n<details><summary>Findings and token usage</summary>\n\n${fence}json\n${clipped}\n${fence}\n\n</details>`
  );
}
