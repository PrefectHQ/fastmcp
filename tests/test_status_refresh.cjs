const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function harness() {
  const elements = new Map();
  const element = () => ({
    children: [],
    dataset: {},
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
    addEventListener() {},
  });
  const pending = [];
  const context = vm.createContext({
    URL,
    Date,
    console,
    window: {
      location: { origin: "http://localhost", href: "http://localhost/" },
    },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      createElement: element,
      querySelectorAll: () => [],
      documentElement: { dataset: {} },
    },
    localStorage: { getItem: () => "dark" },
    ResizeObserver: class {
      observe() {}
    },
    setInterval() {},
    fetch(url) {
      return new Promise((resolve, reject) =>
        pending.push({ url, resolve, reject }),
      );
    },
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../status-site/app.js"), "utf8"),
    context,
  );
  // Chart rendering has separate coverage; exercise real request/triage code.
  vm.runInContext(
    'render = () => { document.getElementById("freshness").textContent = data.as_of; }',
    context,
  );
  return {
    pending,
    elements,
    refresh: () => vm.runInContext("refresh()", context),
    respond(index, body) {
      pending[index].resolve({ ok: true, json: async () => body });
    },
    requests(kind) {
      return pending
        .map((request, index) => ({ ...request, index }))
        .filter((r) => r.url.includes(kind));
    },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const backlog = (at) => ({
  schema: "fastmcp-backlog/1",
  as_of: at,
  samples: [{}],
  daily: [{}],
  events: [],
});
const triage = (at) => ({
  schema: "fastmcp-public-triage/1",
  collection_started_at: at,
  review_leads: [],
  selection: { returned: 0, eligible_in_inventory: 0 },
  open_age_buckets: {
    issues: { lt_7d: 0, "7_30d": 0, "30_90d": 0, gte_90d: 0 },
    prs: { lt_7d: 0, "7_30d": 0, "30_90d": 0, gte_90d: 0 },
  },
});

// app.js automatically starts its initial refresh.
test("refresh stays disabled until both collections settle", async () => {
  const h = harness();
  h.respond(h.requests("backlog")[0].index, backlog("2026-11-03T10:00:00Z"));
  await flush();
  assert.equal(h.elements.get("refresh").disabled, true);
  h.respond(h.requests("triage")[0].index, triage("2026-11-03T10:00:00Z"));
  await flush();
  assert.equal(h.elements.get("refresh").disabled, false);
});

test("older successful responses cannot replace a newer refresh", async () => {
  const h = harness();
  const newer = h.refresh();
  const oldAt = "2026-11-03T10:00:00Z",
    newAt = "2026-11-03T12:00:00Z";
  h.respond(h.requests("triage")[1].index, triage(newAt));
  h.respond(h.requests("backlog")[1].index, backlog(newAt));
  await newer;
  await flush();
  const latestTriage = h.elements.get("triage-freshness").textContent;
  assert.match(latestTriage, /Collection began/);
  h.respond(h.requests("triage")[0].index, triage(oldAt));
  h.respond(h.requests("backlog")[0].index, backlog(oldAt));
  await flush();
  assert.equal(h.elements.get("triage-freshness").textContent, latestTriage);
  assert.equal(h.elements.get("freshness").textContent, newAt);
});

test("older failed requests cannot clear newer results", async () => {
  const h = harness();
  const newer = h.refresh();
  h.respond(h.requests("triage")[1].index, triage("2026-11-03T12:00:00Z"));
  h.respond(h.requests("backlog")[1].index, backlog("2026-11-03T12:00:00Z"));
  await newer;
  await flush();
  const latestTriage = h.elements.get("triage-freshness").textContent;
  assert.match(latestTriage, /Collection began/);
  h.requests("triage")[0].reject(new Error("old failure"));
  h.requests("backlog")[0].reject(new Error("old failure"));
  await flush();
  assert.equal(h.elements.get("triage-freshness").textContent, latestTriage);
  assert.notEqual(h.elements.get("notice")?.hidden, false);
});

function textContent(element) {
  return [
    element.textContent || "",
    ...(element.children || []).map(textContent),
  ].join(" ");
}
for (const [matching, expected] of [
  [true, "marker matches snapshot head and target baseline"],
  [false, "Historical or unbound triage-app review"],
  [null, "target baseline unavailable"],
]) {
  test(`review display respects revision match ${matching}`, async () => {
    const h = harness();
    const report = triage("2026-11-03T12:00:00Z");
    report.review_leads = [
      {
        number: 1,
        title: "Fixture",
        age_days: 10,
        head_sha: "a".repeat(40),
        baseline_sha: matching === null ? null : "b".repeat(40),
        target: "main",
        activity: {
          status: "collected",
          triage_app_review_seen: true,
          triage_app_review_on_snapshot_revision: matching,
        },
        collection_diagnostics:
          matching === null
            ? [
                {
                  stage: "target_baseline",
                  kind: "http_error",
                  http_status: 403,
                  attempts: 1,
                },
              ]
            : [],
      },
    ];
    h.respond(h.requests("triage")[0].index, report);
    h.respond(h.requests("backlog")[0].index, backlog("2026-11-03T12:00:00Z"));
    await flush();
    const text = textContent(h.elements.get("triage-leads"));
    assert.ok(text.includes(expected), text);
    if (matching !== true) assert.ok(!text.includes("marker matches"));
    if (matching === null)
      assert.ok(text.includes("target_baseline: http_error (HTTP 403)"));
  });
}
