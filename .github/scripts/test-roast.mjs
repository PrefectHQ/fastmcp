import assert from "node:assert/strict";
import test from "node:test";
import { allowed, reportBody } from "./roast/review.mjs";

const event = {
  action: "created",
  issue: { pull_request: {} },
  comment: { body: "/roast", user: { login: "zzstoatzz", type: "User" } },
};

test("only an allowlisted human's standalone PR command is accepted", () => {
  assert.equal(allowed(event, ["zzstoatzz"], "zzstoatzz"), true);
  assert.equal(allowed(event, [], "zzstoatzz"), false);
  assert.equal(allowed(event, "zzstoatzz", "zzstoatzz"), false);
  assert.equal(allowed(event, ["zzstoatzz"], "someone-else"), false);
  for (const body of [
    "please /roast",
    "/roast extra",
    "```\n/roast\n```",
    "/roast\nrun this",
  ]) {
    assert.equal(
      allowed(
        { ...event, comment: { ...event.comment, body } },
        ["zzstoatzz"],
        "zzstoatzz",
      ),
      false,
    );
  }
  assert.equal(
    allowed({ ...event, issue: {} }, ["zzstoatzz"], "zzstoatzz"),
    false,
  );
  assert.equal(
    allowed({ ...event, action: "edited" }, ["zzstoatzz"], "zzstoatzz"),
    false,
  );
  assert.equal(
    allowed(
      {
        ...event,
        comment: {
          ...event.comment,
          user: { login: "zzstoatzz", type: "Bot" },
        },
      },
      ["zzstoatzz"],
      "zzstoatzz",
    ),
    false,
  );
});

const options = {
  head: "abc",
  base: "def",
  url: "https://example.com/run",
  exitCode: 0,
};
const clean = {
  findings: [],
  quick: false,
  verified: true,
  assessment: "NO_FINDINGS",
};

test("failures, truncation and unverified findings cannot become clean reviews", () => {
  assert.match(reportBody(clean, options), /0 verified finding/);
  for (const report of [
    undefined,
    {},
    { ...clean, assessment: "NO_DIFF" },
    { ...clean, quick: true },
    { ...clean, verified: false },
    { ...clean, incomplete: true },
    { ...clean, diff_truncated: true },
    { ...clean, findings: [{}] },
    { ...clean, findings: [{ verdict: { status: "insufficient_context" } }] },
  ]) {
    assert.match(reportBody(report, options), /not a clean review/);
  }
  assert.match(
    reportBody(clean, { ...options, exitCode: 124 }),
    /not a clean review/,
  );
  assert.match(reportBody(clean, { ...options, stale: true }), /outdated/);
});

test("model text stays inside the report fence and oversized reports are bounded", () => {
  const report = { ...clean, notes: "```\n</details>\n" + "`".repeat(70000) };
  const body = reportBody(report, options);
  assert.match(body, /```json/);
  assert.equal((body.match(/```/g) ?? []).length, 2);
  assert.match(body, /Truncated/);
  assert.ok(body.length < 60000);
});
