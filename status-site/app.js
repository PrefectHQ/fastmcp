const source = new URL("status.json", window.location.href);
const $ = (id) => document.getElementById(id);
let snapshot;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function safeLink(text, url) {
  const link = el("a", text);
  try {
    const parsed = new URL(url);
    if (
      parsed.origin === "https://github.com" &&
      parsed.pathname.startsWith("/PrefectHQ/fastmcp/")
    )
      link.href = parsed.href;
  } catch {
    /* Missing evidence remains plain text. */
  }
  return link;
}

function item(title, description, state, url, detail) {
  const row = el("article", null, "item");
  const body = el("div");
  const heading = el("h3");
  heading.append(safeLink(title, url));
  body.append(heading);
  if (description) body.append(el("p", description));
  row.append(body, el("span", state, `badge ${state}`));
  if (detail) row.append(el("p", detail, "detail"));
  return row;
}

function render() {
  const doc = snapshot;
  const timestamp = new Date(doc.as_of);
  const old = Date.now() - timestamp.getTime() > 24 * 60 * 60 * 1000;
  $("freshness").textContent =
    `Published ${timestamp.toLocaleString()}. Snapshot updates twice daily; refresh checks for a newer publication.`;
  $("notice").hidden = !old;
  $("notice").textContent =
    "This snapshot is over a day old. Check GitHub before relying on these states.";
  const attention = $("attention");
  attention.replaceChildren();
  const automations = doc.automations.filter(
    (a) => a.id !== "contributor-queue",
  );
  const problems = automations.filter(
    (a) => ["degraded", "off"].includes(a.state) || a.stale,
  );
  for (const a of problems)
    attention.append(
      item(
        a.name,
        a.stale ? "This publisher has stopped reporting fresh data." : a.what,
        a.stale ? "stale" : a.state,
        a.evidence_url,
        "Inspect the source before deciding whether intervention is needed.",
      ),
    );
  const queue = doc.automations.find((a) => a.id === "contributor-queue");
  if (queue?.counts?.waiting > 0) {
    const c = queue.counts;
    attention.append(
      item(
        "Contributor assignments",
        `${c.waiting} PRs are waiting for assignment. ${c.waiting_over_7_days} have waited over a week; the oldest is ${c.oldest_days} days old.`,
        "review",
        queue.evidence_url,
        "Review the linked issues to decide which contributions to accept.",
      ),
    );
  }
  const checks = $("main-checks");
  checks.replaceChildren();
  if (doc.main?.checks?.length) {
    const link = safeLink(doc.main.sha.slice(0, 7), doc.main.url);
    $("commit").replaceWith(Object.assign(link, { id: "commit" }));
    for (const check of doc.main.checks) {
      checks.append(item(check.name, "", check.state, check.url));
      if (check.state === "failed" || check.state === "unknown")
        attention.append(
          item(
            check.name,
            check.state === "failed"
              ? "A check on the current main commit failed."
              : "No completed check is available for the current main commit.",
            check.state,
            check.url || doc.main.url,
          ),
        );
    }
  } else
    checks.append(
      el(
        "p",
        "Main CI is not included in this snapshot. Check the current commit on GitHub.",
      ),
    );
  if (!attention.children.length)
    attention.append(
      el(
        "p",
        "No attention items reported in this snapshot. Check freshness and main CI before relying on it.",
        "empty",
      ),
    );
  const list = $("automations");
  list.replaceChildren();
  const visible = $("attention-only").checked ? problems : automations;
  for (const a of visible)
    list.append(
      item(
        a.name,
        a.what,
        a.stale ? "stale" : a.state,
        a.evidence_url,
        `${a.cadence}. Last success: ${a.last_ok_day || "not reported"}.`,
      ),
    );
  if (!visible.length) list.append(el("p", "No automation problems reported."));
}

async function refresh() {
  $("refresh").disabled = true;
  try {
    const response = await fetch(source, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const doc = await response.json();
    if (
      doc.schema !== "fastmcp-maintenance/1" ||
      !Array.isArray(doc.automations) ||
      !Number.isFinite(Date.parse(doc.as_of))
    )
      throw new Error("Unsupported snapshot");
    snapshot = doc;
    render();
  } catch {
    $("notice").hidden = false;
    $("notice").textContent = snapshot
      ? "Refresh failed. The previous snapshot is still shown; use its publication time."
      : "Could not load status. Open the GitHub snapshot below to check the source.";
    if (!snapshot) {
      $("freshness").textContent = "Status unavailable.";
      $("attention").replaceChildren(
        el("p", "Attention items could not be checked."),
      );
    }
  } finally {
    $("refresh").disabled = false;
  }
}

$("refresh").addEventListener("click", refresh);
$("attention-only").addEventListener("change", () => {
  if (snapshot) render();
});
refresh();
setInterval(refresh, 5 * 60 * 1000);
