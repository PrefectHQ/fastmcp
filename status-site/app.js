const $ = (id) => document.getElementById(id);
let snapshot;
let rankingLimit = 10;
const snapshotUrl =
  window.location.origin === "https://prefecthq.github.io"
    ? "https://raw.githubusercontent.com/PrefectHQ/fastmcp/status/status.json"
    : new URL("status.json", window.location.href);

const number = (value) => (value == null ? "—" : value.toLocaleString());
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function link(text, url) {
  const node = el("a", text);
  try {
    const parsed = new URL(url);
    if (
      parsed.origin === "https://github.com" &&
      parsed.pathname.startsWith("/PrefectHQ/fastmcp/")
    )
      node.href = parsed.href;
  } catch {}
  return node;
}
function issue(a) {
  return ["degraded", "off"].includes(a.state) || a.stale;
}
function attention(title, detail, state, url) {
  const row = el("div", null, "attention-row");
  row.append(
    link(title, url),
    el("span", state, `state ${state}`),
    el("p", detail),
  );
  $("attention").append(row);
}
function svg(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs))
    node.setAttribute(key, value);
  if (text != null) node.textContent = text;
  return node;
}
function chart() {
  const eligible = snapshot.automations.filter((a) => a.daily?.length);
  const selected = $("activity-filter").value;
  const rows = eligible.filter((a) => selected === "all" || a.id === selected);
  const days = new Map();
  for (const a of rows)
    for (const b of a.daily) {
      const day = days.get(b.day) || {
        day: b.day,
        succeeded: 0,
        failed: 0,
        blocked: 0,
      };
      for (const key of ["succeeded", "failed", "blocked"])
        day[key] += b[key] || 0;
      days.set(b.day, day);
    }
  const buckets = [...days.values()].sort((a, b) => a.day.localeCompare(b.day));
  $("chart").replaceChildren();
  $("activity-totals").replaceChildren();
  if (!buckets.length) {
    $("chart").append(el("p", "Activity history unavailable.", "empty"));
    return;
  }
  const series = [
    ["succeeded", "passed", "succeeded"],
    ["failed", "failed", "failed"],
    ["blocked", "blocked", "gate rejections / errors"],
  ];
  for (const [key, cls, label] of series)
    $("activity-totals").append(
      el(
        "span",
        `${number(buckets.reduce((sum, b) => sum + b[key], 0))} ${label}`,
        cls,
      ),
    );
  const graph = svg("svg", {
    viewBox: "0 0 800 210",
    class: "chart",
    role: "img",
    "aria-label":
      "Daily completed automation runs; each bar lists its outcomes",
  });
  const max = Math.max(
    1,
    ...buckets.map((b) => b.succeeded + b.failed + b.blocked),
  );
  for (let i = 0; i <= 3; i++) {
    const y = 175 - i * 50;
    graph.append(
      svg("line", { x1: 40, x2: 790, y1: y, y2: y, class: "grid" }),
      svg(
        "text",
        { x: 32, y: y + 4, "text-anchor": "end" },
        Math.round((max * i) / 3),
      ),
    );
  }
  buckets.forEach((b, i) => {
    const step = 750 / buckets.length,
      x = 40 + i * step,
      width = Math.min(56, step * 0.6);
    const label = `${b.day}: ${b.succeeded} succeeded, ${b.failed} failed, ${b.blocked} gate rejections or errors`;
    const group = svg("g", { class: "bar", tabindex: 0, "aria-label": label });
    group.append(svg("title", {}, label));
    let y = 175;
    for (const [key, cls] of series) {
      const height = (b[key] / max) * 150;
      y -= height;
      group.append(
        svg("rect", {
          x: x + (step - width) / 2,
          y,
          width,
          height,
          class: cls,
        }),
      );
    }
    graph.append(
      group,
      svg(
        "text",
        { x: x + step / 2, y: 201, "text-anchor": "middle" },
        b.day.slice(5),
      ),
    );
  });
  $("chart").append(graph);
  const missing = snapshot.automations.filter(
    (a) => a.id !== "contributor-queue" && !a.daily?.length,
  );
  if (selected === "all" && missing.length)
    $("chart").append(
      el(
        "p",
        `History unavailable for ${missing.map((a) => a.name).join(", ")}.`,
        "caption",
      ),
    );
}
function automations() {
  const open = new Set(
    [...$("automations").querySelectorAll("details[open]")].map(
      (n) => n.dataset.id,
    ),
  );
  $("automations").replaceChildren();
  for (const a of snapshot.automations.filter(
    (a) => a.id !== "contributor-queue",
  )) {
    if ($("attention-only").checked && !issue(a)) continue;
    const row = el("details");
    row.dataset.id = a.id;
    row.open = open.has(a.id);
    const summary = el("summary");
    const count = Object.values(a.counts || {}).reduce((sum, n) => sum + n, 0);
    const state = a.stale ? "stale" : a.state;
    summary.append(
      el("span", a.name, "name"),
      el("span", state, `state ${state}`),
      el(
        "span",
        `${Object.keys(a.counts || {}).length ? number(count) : "—"} outcomes`,
        "count",
      ),
    );
    const detail = el("div", null, "detail");
    detail.append(
      el("p", a.what),
      el("p", `${a.cadence} · last success ${a.last_ok_day || "unavailable"}`),
      link("inspect runs ↗", a.evidence_url),
    );
    row.append(summary, detail);
    $("automations").append(row);
  }
  if (!$("automations").childElementCount)
    $("automations").append(el("p", "No automation issues reported.", "empty"));
}

function ranking() {
  const data = snapshot.attention;
  $("ranking").replaceChildren();
  if (!data || data.schema !== "fastmcp-attention/1") {
    $("ranking-coverage").textContent =
      "Issue ranking unavailable. Queue age below is not a priority ranking.";
    $("ranking-more").hidden = true;
    return;
  }
  const judged =
    data.judged === data.examined
      ? `judge: ${data.judge}`
      : `${data.judged}/${data.examined} model-assessed; remaining issues use facts and labels only`;
  $("ranking-coverage").textContent =
    `${data.examined} ${data.has_more ? "newest open issues examined; older issues not included" : "open issues examined; complete at collection"}. ${judged}. Ranked ${new Date(data.as_of).toLocaleString()}. Contributor assessments excluded.`;
  const kind = $("ranking-kind").value;
  $("ranking-kind").replaceChildren(
    Object.assign(el("option", "all kinds"), { value: "all" }),
  );
  for (const value of [...new Set(data.items.map((i) => i.kind))].sort())
    $("ranking-kind").append(Object.assign(el("option", value), { value }));
  if ([...$("ranking-kind").options].some((o) => o.value === kind))
    $("ranking-kind").value = kind;
  const items = data.items.filter(
    (i) =>
      ($("ranking-kind").value === "all" ||
        i.kind === $("ranking-kind").value) &&
      (!$("ranking-unanswered").checked || i.maintainer_replied === false),
  );
  for (const item of items.slice(0, rankingLimit)) {
    const row = el("details", null, "ranked-issue");
    const summary = el("summary");
    const heading = el("div", null, "rank-heading");
    heading.append(
      el("span", `#${item.number}`, "muted"),
      el("span", item.title),
    );
    const reasons = item.reasons
      .slice(0, 3)
      .map((r) => `${r.contribution > 0 ? "+" : "−"} ${r.text}`)
      .join(" · ");
    heading.append(
      el("p", reasons || "No weighted signals available.", "caption"),
    );
    summary.append(
      el("span", item.score.toFixed(1), "rank-score"),
      heading,
      el("span", "+", "muted"),
    );
    const detail = el("div", null, "detail");
    detail.append(
      el(
        "p",
        `${item.kind} · ${item.judged ? "model-assessed" : "facts and labels only"} · ${item.maintainer_replied ? "maintainer replied" : item.maintainer_replied === false ? "no maintainer reply found" : "reply history incomplete"} · ${item.assigned ? "assigned" : "unassigned"}`,
      ),
    );
    for (const reason of item.reasons)
      detail.append(
        el(
          "p",
          `${reason.contribution > 0 ? "+" : ""}${reason.contribution.toFixed(2)} ${reason.text}`,
        ),
      );
    detail.append(link("open issue ↗", item.url));
    for (const pr of item.linked_prs)
      detail.append(
        link(
          ` · fix #${pr} ↗`,
          `https://github.com/PrefectHQ/fastmcp/pull/${pr}`,
        ),
      );
    row.append(summary, detail);
    $("ranking").append(row);
  }
  if (!items.length)
    $("ranking").append(el("p", "No issues match these filters.", "empty"));
  $("ranking-more").hidden = items.length <= rankingLimit;
  $("ranking-more").textContent =
    `show more (${items.length - rankingLimit} remaining)`;
}

function render() {
  const doc = snapshot,
    queue = doc.automations.find((a) => a.id === "contributor-queue");
  const problems = doc.automations.filter(issue),
    checks = doc.main?.checks || [];
  const old = Date.now() - Date.parse(doc.as_of) > 86400000;
  const green = checks.length && checks.every((c) => c.state === "ok");
  $("health").textContent = old
    ? "◌ snapshot is stale"
    : green
      ? "● main checks passing"
      : "◌ main needs verification";
  $("health").className = old || !green ? "warning" : "";
  $("freshness").textContent =
    `updated ${new Date(doc.as_of).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`;
  $("notice").hidden = !old;
  $("notice").textContent =
    "Snapshot is over 24 hours old. Check GitHub for current evidence.";
  $("metrics").replaceChildren();
  const c = queue?.counts;
  for (const [value, label, warn] of [
    [doc.attention?.examined, "issues ranked", false],
    [
      doc.attention?.items.filter((i) => i.maintainer_replied === false).length,
      "without maintainer reply",
      false,
    ],
    [c?.waiting, "awaiting assignment", false],
    [problems.length, "automation issues", problems.length > 0],
  ]) {
    const metric = el("div", null, `metric ${warn ? "warn" : ""}`);
    metric.append(el("strong", number(value)), el("span", label));
    $("metrics").append(metric);
  }
  $("attention").replaceChildren();
  if (c?.waiting)
    attention(
      "Contributor assignments",
      `${c.waiting} open requests; ${c.waiting_over_7_days} older than a week. Review their linked issues before assigning.`,
      "review",
      queue.evidence_url,
    );
  for (const a of problems)
    attention(a.name, a.what, a.stale ? "stale" : a.state, a.evidence_url);
  for (const check of checks.filter((c) =>
    ["failed", "unknown"].includes(c.state),
  ))
    attention(
      check.name,
      check.state === "failed"
        ? "Check failed on the current main commit."
        : "No conclusive result for this main commit.",
      check.state,
      check.url || doc.main?.url,
    );
  if (!checks.length)
    attention(
      "Main checks",
      "Current commit evidence is unavailable.",
      "unknown",
      doc.main?.url,
    );
  if (!$("attention").childElementCount)
    $("attention").append(
      el("p", "No action indicated by this snapshot.", "empty"),
    );
  $("main-checks").replaceChildren();
  if (doc.main?.sha) {
    const commit = link(doc.main.sha.slice(0, 7), doc.main.url);
    commit.id = "commit";
    $("commit").replaceWith(commit);
  }
  for (const check of checks) {
    const item = link("", check.url);
    item.className = "check";
    item.append(
      el("span", check.name),
      el(
        "span",
        `${check.state === "ok" ? "●" : "◌"} ${check.state === "ok" ? "passing" : check.state}`,
        `state ${check.state}`,
      ),
    );
    $("main-checks").append(item);
  }
  if (!checks.length)
    $("main-checks").append(el("p", "Checks unavailable", "empty"));
  const selection = $("activity-filter").value;
  $("activity-filter").replaceChildren(
    Object.assign(el("option", "all automation"), { value: "all" }),
  );
  for (const a of doc.automations.filter((a) => a.daily?.length))
    $("activity-filter").append(
      Object.assign(el("option", a.name), { value: a.id }),
    );
  if ([...$("activity-filter").options].some((o) => o.value === selection))
    $("activity-filter").value = selection;
  chart();
  automations();
  ranking();
  $("queue").replaceChildren();
  if (queue) {
    const all = link(
      `all ${number(c?.waiting)} requests ↗`,
      queue.evidence_url,
    );
    all.id = "queue-link";
    $("queue-link").replaceWith(all);
  }
  for (const p of queue?.items || []) {
    const row = el("div", null, "queue-row");
    row.append(
      link(`#${p.number}`, p.url),
      link(p.title, p.url),
      el("span", `${p.age_days}d`, "age"),
    );
    row.firstChild.className = "number";
    $("queue").append(row);
  }
  if (!$("queue").childElementCount)
    $("queue").append(
      el(
        "p",
        c?.waiting === 0
          ? "No requests waiting."
          : "Request details unavailable. Follow all requests for the current list.",
        "empty",
      ),
    );
}
async function refresh() {
  $("refresh").disabled = true;
  try {
    const response = await fetch(snapshotUrl, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Snapshot request failed");
    const doc = await response.json();
    if (
      doc.schema !== "fastmcp-maintenance/1" ||
      !Array.isArray(doc.automations) ||
      !Number.isFinite(Date.parse(doc.as_of))
    )
      throw new Error("Invalid snapshot");
    snapshot = doc;
    render();
  } catch {
    $("notice").hidden = false;
    $("notice").textContent = snapshot
      ? "Refresh failed. Displaying the last loaded snapshot."
      : "Snapshot unavailable. Use the GitHub snapshot link below.";
    $("health").textContent = "◌ freshness unverified";
    $("health").className = "warning";
  } finally {
    $("refresh").disabled = false;
  }
}
try {
  document.documentElement.dataset.theme =
    localStorage.getItem("maintenance-theme") || "dark";
} catch {}
$("theme").addEventListener("click", () => {
  const theme =
    document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("maintenance-theme", theme);
  } catch {}
});
$("refresh").addEventListener("click", refresh);
$("activity-filter").addEventListener("change", chart);
$("attention-only").addEventListener("change", automations);
document.querySelector('footer a[href="status.json"]').href = snapshotUrl;
refresh();
setInterval(refresh, 300000);

$("ranking-kind").addEventListener("change", () => {
  rankingLimit = 10;
  ranking();
});
$("ranking-unanswered").addEventListener("change", () => {
  rankingLimit = 10;
  ranking();
});
$("ranking-more").addEventListener("click", () => {
  rankingLimit += 10;
  ranking();
});
