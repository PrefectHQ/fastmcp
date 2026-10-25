const $ = (id) => document.getElementById(id);
const DATA_URL =
  window.location.origin === "https://prefecthq.github.io"
    ? "https://raw.githubusercontent.com/PrefectHQ/fastmcp/status/backlog.json"
    : new URL("backlog.json", window.location.href).href;
const TZ = "America/Chicago";
const DAY = 86400000;
const labels = {
  issues_opened: "Issues opened",
  prs_opened: "PRs opened",
  completed: "Completed",
  not_planned: "Not planned",
  other_closed: "Unspecified",
  merged: "Merged",
  closed_unmerged: "Closed unmerged",
};
const colors = {
  issues_opened: "--opened",
  prs_opened: "--opened",
  completed: "--completed",
  not_planned: "--not-planned",
  other_closed: "--other",
  merged: "--merged",
  closed_unmerged: "--closed-unmerged",
};
let data,
  days = 30,
  category = "issues",
  selectedDay;
const number = (n) => n.toLocaleString();
const dateLabel = (at, options = {}) =>
  new Date(at).toLocaleString("en-US", {
    timeZone: TZ,
    month: "short",
    day: "numeric",
    ...options,
  });
const dayLabel = (day) => dateLabel(`${day}T12:00:00Z`);
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function svg(tag, attrs, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text != null) el.textContent = text;
  return el;
}
function color(key) {
  return `var(${colors[key]})`;
}
function selectedDaily() {
  return days === "all" ? data.daily : data.daily.slice(-days);
}
function chartFrame(id, label, height = 250) {
  const host = $(id);
  host.replaceChildren();
  const width = Math.max(280, host.clientWidth);
  const graph = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart",
    role: "img",
    tabindex: 0,
    "aria-label": label,
  });
  host.append(graph);
  return {
    graph,
    width,
    height,
    left: 42,
    right: width - 12,
    top: 16,
    bottom: height - 30,
  };
}
function horizontal(graph, left, right, y, label) {
  graph.append(
    svg("line", { x1: left, x2: right, y1: y, y2: y, class: "grid" }),
    svg("text", { x: left - 8, y: y + 4, "text-anchor": "end" }, label),
  );
}
function dateTicks(graph, entries, x, bottom, width, label) {
  const count = width < 500 ? 3 : 5;
  const indices = new Set(
    Array.from({ length: count }, (_, i) =>
      Math.round((i * (entries.length - 1)) / (count - 1)),
    ),
  );
  for (const i of indices)
    graph.append(
      svg(
        "text",
        {
          x: x(entries[i], i),
          y: bottom + 23,
          "text-anchor":
            i === 0 ? "start" : i === entries.length - 1 ? "end" : "middle",
        },
        label(entries[i]),
      ),
    );
}
function interactions(graph, entries, nearest, show, choose) {
  let index = entries.length - 1;
  const pointAt = (event) => {
    const rect = graph.getBoundingClientRect();
    return nearest(
      ((event.clientX - rect.left) * graph.viewBox.baseVal.width) / rect.width,
    );
  };
  graph.addEventListener("pointermove", (event) => {
    index = pointAt(event);
    show(index);
  });
  graph.addEventListener("click", (event) => {
    if (event.detail !== 0) index = pointAt(event);
    show(index);
    if (choose) choose(index);
  });
  graph.addEventListener("focus", () => show(index));
  graph.addEventListener("keydown", (event) => {
    if (
      !["ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(
        event.key,
      )
    )
      return;
    event.preventDefault();
    if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
    if (event.key === "ArrowRight")
      index = Math.min(entries.length - 1, index + 1);
    if (event.key === "Home") index = 0;
    if (event.key === "End") index = entries.length - 1;
    show(index);
    if (["Enter", " "].includes(event.key) && choose) choose(index);
  });
}
function backlog() {
  const end = Date.parse(data.as_of),
    start = days === "all" ? Date.parse(data.samples[0].at) : end - days * DAY;
  let rows = data.samples.filter((row) => Date.parse(row.at) >= start);
  const before = data.samples
    .filter((row) => Date.parse(row.at) < start)
    .at(-1);
  if (before) rows = [before, ...rows];
  if (!rows.length) {
    $("backlog-chart").textContent = "No snapshots in this range.";
    return;
  }
  const { graph, left, right, top, bottom, width } = chartFrame(
    "backlog-chart",
    "Open issues and pull requests over time. Use left and right arrow keys to inspect snapshots.",
    270,
  );
  const max = Math.max(
    10,
    ...rows.flatMap((row) => [row.issues.open, row.prs.open]),
  );
  const ceiling = Math.ceil(max / 50) * 50;
  const x = (row) =>
    left +
    ((Math.max(start, Date.parse(row.at)) - start) / (end - start || DAY)) *
      (right - left);
  const y = (value) => bottom - (value / ceiling) * (bottom - top);
  for (let i = 0; i <= 4; i++)
    horizontal(
      graph,
      left,
      right,
      y((ceiling * i) / 4),
      Math.round((ceiling * i) / 4),
    );
  for (const [key, stroke] of [
    ["issues", "var(--issue)"],
    ["prs", "var(--pr)"],
  ]) {
    const historical = rows.filter((row) => row.source !== "live");
    const recorded = rows.filter((row) => row.source === "live");
    if (historical.length && recorded.length) historical.push(recorded[0]);
    for (const [segment, dashed] of [
      [historical, true],
      [recorded, false],
    ]) {
      if (!segment.length) continue;
      graph.append(
        svg("path", {
          d: segment
            .map((row, i) => `${i ? "L" : "M"}${x(row)},${y(row[key].open)}`)
            .join(" "),
          class: "series",
          stroke,
          "stroke-dasharray": dashed ? "5 5" : "none",
        }),
      );
      const last = segment.at(-1);
      graph.append(
        svg("circle", {
          cx: x(last),
          cy: y(last[key].open),
          r: 3,
          fill: stroke,
        }),
      );
    }
  }
  const tickCount = width < 500 ? 3 : 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => ({
    at: new Date(start + ((end - start) * i) / (tickCount - 1)).toISOString(),
  }));
  dateTicks(graph, ticks, x, bottom, width, (row) => dateLabel(row.at));
  const cursor = svg("line", {
    y1: top,
    y2: bottom,
    x1: right,
    x2: right,
    class: "cursor",
    visibility: "hidden",
  });
  graph.append(cursor);
  const show = (index) => {
    const row = rows[index];
    cursor.setAttribute("x1", x(row));
    cursor.setAttribute("x2", x(row));
    cursor.setAttribute("visibility", "visible");
    $("backlog-readout").textContent =
      `${dateLabel(row.at, { year: "numeric", hour: "numeric", minute: "2-digit" })} Central: ${number(row.issues.open)} issues · ${number(row.prs.open)} PRs${row.prs.draft != null ? ` (${row.prs.draft} draft)` : ""} · ${row.source === "live" ? "recorded" : "reconstructed"}`;
  };
  interactions(
    graph,
    rows,
    (pos) =>
      rows.reduce(
        (best, row, i) =>
          Math.abs(x(row) - pos) < Math.abs(x(rows[best]) - pos) ? i : best,
        0,
      ),
    show,
  );
  const latest = data.samples.at(-1),
    first = rows[0];
  $("totals").replaceChildren();
  for (const [key, label, cls] of [
    ["issues", "open issues", "issue"],
    ["prs", "open PRs", "pr"],
  ]) {
    const item = node("div", null, `total ${cls}`),
      delta = latest[key].open - first[key].open;
    item.append(
      node("strong", number(latest[key].open)),
      node("span", label),
      node("small", `${delta > 0 ? "+" : ""}${number(delta)} in view`),
    );
    $("totals").append(item);
  }
}
function recent() {
  $("recent").replaceChildren();
  for (const [row, label] of [
    [data.daily.at(-2), "Yesterday"],
    [data.daily.at(-1), "Today so far"],
  ]) {
    const box = node("article", null, "day-summary"),
      closed = row.completed + row.not_planned + row.other_closed;
    box.append(node("h3", `${label} · ${dayLabel(row.day)}`));
    for (const [text, kind] of [
      [`${row.issues_opened} issues opened · ${closed} closed`, "issues"],
      [
        `${row.prs_opened} PRs opened · ${row.merged} merged · ${row.closed_unmerged} closed unmerged`,
        "prs",
      ],
    ]) {
      const line = node("p"),
        button = node("button", text);
      button.addEventListener("click", () => {
        category = kind;
        setCategory();
        activity();
        detail(row.day, true);
        $("activity-detail").scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
      });
      line.append(button);
      box.append(line);
    }
    box.append(
      node(
        "small",
        `${row.completed} completed · ${row.not_planned} not planned${row.other_closed ? ` · ${row.other_closed} unspecified` : ""}`,
      ),
    );
    $("recent").append(box);
  }
}
function activity() {
  const rows = selectedDaily(),
    opened = `${category}_opened`,
    outcomes =
      category === "issues"
        ? ["completed", "not_planned", "other_closed"]
        : ["merged", "closed_unmerged"];
  const keys = [opened, ...outcomes];
  $("activity-legend").replaceChildren();
  for (const key of keys) {
    const item = node("span"),
      chip = node("span", null, "key");
    chip.style.background = color(key);
    item.append(
      chip,
      node(
        "span",
        `${labels[key]} ${number(rows.reduce((sum, row) => sum + row[key], 0))}`,
      ),
    );
    $("activity-legend").append(item);
  }
  const { graph, left, right, top, bottom, width } = chartFrame(
    "activity-chart",
    `Daily ${category === "issues" ? "issue" : "pull request"} openings and closure outcomes. Use arrow keys then Enter to inspect a day.`,
    270,
  );
  const max = Math.max(
    5,
    ...rows.map((row) =>
      Math.max(
        row[opened],
        outcomes.reduce((sum, key) => sum + row[key], 0),
      ),
    ),
  );
  const limit = Math.ceil(max / 5) * 5,
    middle = (top + bottom) / 2,
    scale = (bottom - top) / 2 / limit;
  for (const value of [-limit, -limit / 2, 0, limit / 2, limit])
    horizontal(
      graph,
      left,
      right,
      middle - value * scale,
      Math.round(Math.abs(value)),
    );
  const step = (right - left) / rows.length,
    barWidth = Math.max(1, Math.min(24, step * 0.68));
  const x = (_, index) => left + (index + 0.5) * step;
  rows.forEach((row, index) => {
    const group = svg("g", {}, null);
    group.append(
      svg("rect", {
        x: x(row, index) - barWidth / 2,
        y: middle - row[opened] * scale,
        width: barWidth,
        height: row[opened] * scale,
        fill: color(opened),
      }),
    );
    let offset = middle;
    for (const key of outcomes) {
      const height = row[key] * scale;
      group.append(
        svg("rect", {
          x: x(row, index) - barWidth / 2,
          y: offset,
          width: barWidth,
          height,
          fill: color(key),
        }),
      );
      offset += height;
    }
    graph.append(group);
  });
  dateTicks(graph, rows, x, bottom, width, (row) => dayLabel(row.day));
  const cursor = svg("rect", {
    x: left,
    y: top,
    width: Math.max(step, 2),
    height: bottom - top,
    fill: "var(--text)",
    opacity: 0.07,
    visibility: "hidden",
  });
  graph.append(cursor);
  const show = (index) => {
    const row = rows[index];
    cursor.setAttribute("x", left + index * step);
    cursor.setAttribute("visibility", "visible");
    $("activity-readout").textContent =
      `${dayLabel(row.day)}: ${keys.map((key) => `${row[key]} ${labels[key].toLowerCase()}`).join(" · ")}`;
  };
  interactions(
    graph,
    rows,
    (pos) =>
      Math.max(0, Math.min(rows.length - 1, Math.floor((pos - left) / step))),
    show,
    (index) => detail(rows[index].day, true),
  );
  detail(selectedDay || rows.at(-1).day, false);
}
function detail(day, open) {
  selectedDay = day;
  $("detail-title").textContent =
    `${dayLabel(day)} · ${category === "issues" ? "issues" : "pull requests"}`;
  if (open) $("activity-detail").open = true;
  const rows = data.events.filter(
    (item) => item.day === day && item.kind === category,
  );
  $("detail-items").replaceChildren();
  if (!rows.length) {
    $("detail-items").append(
      node(
        "p",
        day < data.daily.at(-31).day
          ? "Item details are kept for the last 30 days. Totals above include older activity."
          : "No recorded activity for this day.",
        "caption",
      ),
    );
    return;
  }
  for (const item of rows) {
    const row = node("div", null, "work-row"),
      link = node("a", `#${item.number}`);
    link.href = `https://github.com/PrefectHQ/fastmcp/${item.kind === "prs" ? "pull" : "issues"}/${item.number}`;
    row.append(
      link,
      node("span", item.title),
      node("span", labels[item.event], "state"),
    );
    $("detail-items").append(row);
  }
}
function setCategory() {
  document
    .querySelectorAll("[data-kind]")
    .forEach((button) =>
      button.setAttribute("aria-pressed", button.dataset.kind === category),
    );
}
function render() {
  backlog();
  recent();
  activity();
  const stale = Date.now() - Date.parse(data.as_of) > 2 * 60 * 60 * 1000;
  $("freshness").textContent =
    `Updated ${dateLabel(data.as_of, { year: "numeric", hour: "numeric", minute: "2-digit" })} Central${stale ? " · update overdue" : ""}`;
  $("notice").hidden = !stale;
  $("notice").textContent =
    "The latest snapshot is over two hours old. Counts may have changed; follow GitHub for the current queue.";
}
async function refresh() {
  $("refresh").disabled = true;
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("Data unavailable");
    const next = await response.json();
    if (
      next.schema !== "fastmcp-backlog/1" ||
      !next.samples?.length ||
      !next.daily?.length ||
      !Array.isArray(next.events) ||
      !Number.isFinite(Date.parse(next.as_of))
    )
      throw new Error("Invalid data");
    data = next;
    render();
  } catch {
    $("notice").hidden = false;
    $("notice").textContent = data
      ? "Refresh failed. Showing the last loaded data; check its timestamp above."
      : "Backlog data is unavailable. Refresh to try again, or open the GitHub source below.";
  } finally {
    $("refresh").disabled = false;
  }
}
document.querySelectorAll("[data-days]").forEach((button) =>
  button.addEventListener("click", () => {
    days = button.dataset.days === "all" ? "all" : Number(button.dataset.days);
    document
      .querySelectorAll("[data-days]")
      .forEach((item) => item.setAttribute("aria-pressed", item === button));
    if (data) {
      backlog();
      activity();
    }
  }),
);
document.querySelectorAll("[data-kind]").forEach((button) =>
  button.addEventListener("click", () => {
    category = button.dataset.kind;
    setCategory();
    if (data) activity();
  }),
);
try {
  document.documentElement.dataset.theme =
    localStorage.getItem("maintenance-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch {}
$("theme").addEventListener("click", () => {
  const theme =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("maintenance-theme", theme);
  } catch {}
});
$("refresh").addEventListener("click", refresh);
$("data-link").href = DATA_URL;
$("export").addEventListener("click", () => {
  if (!data) return;
  const csv =
    "timestamp,source,open_issues,open_prs,draft_prs\n" +
    data.samples
      .map((row) =>
        [
          row.at,
          row.source,
          row.issues.open,
          row.prs.open,
          row.prs.draft ?? "",
        ].join(","),
      )
      .join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = node("a");
  a.href = url;
  a.download = "fastmcp-backlog.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
let resize;
new ResizeObserver(() => {
  clearTimeout(resize);
  resize = setTimeout(() => {
    if (data) {
      backlog();
      activity();
    }
  }, 100);
}).observe($("backlog-chart"));
refresh();
setInterval(refresh, 300000);
