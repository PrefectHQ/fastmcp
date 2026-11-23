// Property-based test coverage map. Independent of the backlog charts: its own
// data file, its own failures, its own refresh.
(() => {
  const el = (id) => document.getElementById(id);
  const PBT_URL =
    window.location.origin === "https://prefecthq.github.io"
      ? "https://raw.githubusercontent.com/PrefectHQ/fastmcp/status/pbt-coverage.json"
      : new URL("pbt-coverage.json", window.location.href).href;
  const COMMIT_URL = "https://github.com/PrefectHQ/fastmcp/commit/";
  const SOURCE_URL = "https://github.com/PrefectHQ/fastmcp/blob/";
  const RUNS_URL =
    "https://github.com/PrefectHQ/fastmcp/actions/workflows/pbt-coverage.yml";
  // Rebuilt on every relevant push and daily, so anything older than a day
  // and a half means the rebuild is failing or not running.
  const STALE_HOURS = 36;
  // #pbt-coverage/fastmcp.server.proxy links to one module.
  const SECTION = "pbt-coverage";
  const moduleHash = (name) => `#${SECTION}/${name}`;
  const BINS = [
    { min: 0, label: "Not reached", token: "--pbt-0" },
    { min: 0.000001, label: "Under 10%", token: "--pbt-1" },
    { min: 0.1, label: "10–30%", token: "--pbt-2" },
    { min: 0.3, label: "30–60%", token: "--pbt-3" },
    { min: 0.6, label: "60% or more", token: "--pbt-4" },
  ];
  let report,
    scope = "public",
    cells = [],
    selected = null;

  const share = (m) => (m.statements ? m.hit / m.statements : 0);
  const pct = (n) =>
    n === 0 ? "0%" : n < 0.01 ? "<1%" : `${Math.floor(n * 100)}%`;
  const bin = (m) => {
    const s = share(m);
    for (let i = BINS.length - 1; i >= 0; i--) if (s >= BINS[i].min) return i;
    return 0;
  };
  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function shape(tag, attrs, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  }

  // Squarified treemap (Bruls, Huizing, van Wijk). Items need a `size`.
  function squarify(items, x, y, w, h) {
    const total = items.reduce((sum, item) => sum + item.size, 0);
    const out = [];
    if (!total || w <= 0 || h <= 0) return out;
    const scale = (w * h) / total;
    let queue = items
      .map((item) => ({ item, area: item.size * scale }))
      .sort((a, b) => b.area - a.area);
    const worst = (row, side) => {
      const sum = row.reduce((s, r) => s + r.area, 0);
      let max = 0;
      for (const r of row) {
        const ratio = Math.max(
          (side * side * r.area) / (sum * sum),
          (sum * sum) / (side * side * r.area),
        );
        max = Math.max(max, ratio);
      }
      return max;
    };
    while (queue.length) {
      const side = Math.min(w, h);
      const row = [queue.shift()];
      while (
        queue.length &&
        worst([...row, queue[0]], side) <= worst(row, side)
      )
        row.push(queue.shift());
      const sum = row.reduce((s, r) => s + r.area, 0);
      const thickness = sum / side;
      let offset = 0;
      for (const r of row) {
        const length = r.area / thickness;
        out.push(
          w >= h
            ? { item: r.item, x, y: y + offset, w: thickness, h: length }
            : { item: r.item, x: x + offset, y, w: length, h: thickness },
        );
        offset += length;
      }
      if (w >= h) {
        x += thickness;
        w -= thickness;
      } else {
        y += thickness;
        h -= thickness;
      }
    }
    return out;
  }

  // Group by package under fastmcp; split any package holding over a third of
  // the visible statements one level deeper so the server doesn't swallow it.
  function groups(modules) {
    // Directories under fastmcp/, so a package __init__ sits with its package.
    const key = (m, depth) =>
      m.path.split("/").slice(2, -1).slice(0, depth).join(".") || "fastmcp";
    const total = modules.reduce((s, m) => s + m.statements, 0);
    const first = new Map();
    for (const m of modules) {
      const k = key(m, 1);
      first.set(k, [...(first.get(k) || []), m]);
    }
    const out = [];
    for (const [k, members] of first) {
      const size = members.reduce((s, m) => s + m.statements, 0);
      if (size > total / 3 && members.some((m) => key(m, 2) !== k)) {
        const second = new Map();
        for (const m of members) {
          const k2 = key(m, 2);
          second.set(k2, [...(second.get(k2) || []), m]);
        }
        for (const [k2, sub] of second) out.push({ name: k2, members: sub });
      } else out.push({ name: k, members });
    }
    return out.map((g) => ({
      ...g,
      size: g.members.reduce((s, m) => s + m.statements, 0),
    }));
  }

  function visible() {
    return report.modules.filter(
      (m) => m.statements > 0 && (scope === "all" || m.public),
    );
  }

  function totals(modules) {
    const statements = modules.reduce((s, m) => s + m.statements, 0);
    const hit = modules.reduce((s, m) => s + m.hit, 0);
    const touched = modules.filter((m) => m.hit > 0).length;
    const host = el("pbt-totals");
    host.replaceChildren();
    const stat = (value, label) => {
      const box = make("div", null, "total");
      box.append(make("strong", value), make("small", label));
      host.append(box);
    };
    stat(pct(statements ? hit / statements : 0), "of function-body lines");
    stat(`${touched}/${modules.length}`, "modules reached");
    stat(
      report.suite.tests_reaching_fastmcp.toLocaleString(),
      "property tests in the suite",
    );
  }

  // The module a URL names, if any. A broken escape reads as raw text.
  function linked() {
    let hash = window.location.hash.slice(1);
    try {
      hash = decodeURIComponent(hash);
    } catch {
      // Keep the raw hash; it just won't name a module.
    }
    return hash.startsWith(`${SECTION}/`)
      ? hash.slice(SECTION.length + 1)
      : null;
  }

  function setScope(next) {
    scope = next;
    document
      .querySelectorAll("[data-pbt-scope]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", b.dataset.pbtScope === next),
      );
  }

  // Select and draw the module the URL names, widening to all modules for
  // an internal one. Returns false, drawing nothing, for an unknown name.
  function follow() {
    const name = linked();
    const m = name && report?.modules.find((x) => x.module === name);
    if (!m || !m.statements) return false;
    if (!m.public) setScope("all");
    selected = m.module;
    draw();
    return true;
  }

  // Keep the address bar pointing at the selected module without adding
  // history entries or jumping the page.
  function remember(name) {
    try {
      history.replaceState(null, "", moduleHash(name));
    } catch {
      // Some embedded viewers refuse history changes; selection still works.
    }
  }

  function legend() {
    const host = el("pbt-legend");
    host.replaceChildren();
    for (const b of BINS) {
      const item = make("span");
      const key = make("span", null, "key");
      key.style.background = `var(${b.token})`;
      if (b.token === "--pbt-0") key.style.outline = "1px solid var(--line)";
      item.append(key, b.label);
      host.append(item);
    }
  }

  function describe(m) {
    const host = el("pbt-readout");
    host.replaceChildren();
    if (!m) {
      host.textContent =
        "Hover, tap, or use arrow keys to inspect a module. Each box is sized by its function-body lines.";
      return;
    }
    const head = make("div");
    const link = make("a", m.module);
    link.href = `${SOURCE_URL}${report.source_commit || "main"}/${m.path}`;
    const anchor = make("a", "#", "anchor");
    anchor.href = moduleHash(m.module);
    anchor.setAttribute("aria-label", `Link to ${m.module}`);
    anchor.title = "Link to this module";
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      remember(m.module);
      navigator.clipboard
        ?.writeText(anchor.href)
        .then(() => (anchor.textContent = "# copied"))
        .catch(() => {});
    });
    head.append(
      link,
      anchor,
      ` · ${pct(share(m))} of ${m.statements.toLocaleString()} lines · ${m.functions_hit}/${m.functions} functions · ${m.tests} tests${m.public ? "" : " · internal"}`,
    );
    host.append(head);
    if (m.symbols.length) {
      const list = make("div", null, "pbt-symbols");
      for (const s of m.symbols) {
        const reached = s.hit > 0;
        const tag = make(
          "span",
          `${reached ? "●" : "○"} ${s.name}${s.statements ? ` ${s.hit}/${s.statements}` : ""}`,
          reached ? "pbt-reached" : "pbt-missed",
        );
        tag.title = reached
          ? "Reached by a property-based test"
          : s.kind === "value"
            ? "Documented value, no function body to measure"
            : "Not reached by any property-based test";
        list.append(tag);
      }
      host.append(list);
    }
  }

  function draw() {
    const host = el("pbt-map");
    host.replaceChildren();
    const modules = visible();
    totals(modules);
    if (!modules.length) {
      host.textContent = "No modules to show.";
      return;
    }
    const width = Math.max(280, host.clientWidth);
    const height = width < 600 ? 520 : 460;
    const graph = shape("svg", {
      viewBox: `0 0 ${width} ${height}`,
      class: "chart pbt-chart",
      role: "img",
      tabindex: 0,
      "aria-label": `Treemap of ${modules.length} fastmcp modules colored by property-based test coverage. Use arrow keys to step through modules; the module table below has the same data.`,
    });
    host.append(graph);
    cells = [];
    const header = 17;
    for (const g of squarify(groups(modules), 0, 0, width, height)) {
      const group = shape("g", {});
      graph.append(group);
      group.append(
        shape("rect", {
          x: g.x + 1,
          y: g.y + 1,
          width: Math.max(0, g.w - 2),
          height: Math.max(0, g.h - 2),
          rx: 4,
          class: "pbt-group",
        }),
      );
      const showHeader = g.w > 44 && g.h > header + 12;
      if (showHeader)
        group.append(
          shape(
            "text",
            { x: g.x + 6, y: g.y + 13, class: "pbt-group-label" },
            clip(g.item.name, g.w - 10, 6.3),
          ),
        );
      const top = showHeader ? header : 2;
      for (const c of squarify(
        g.item.members.map((m) => ({ ...m, size: m.statements })),
        g.x + 2,
        g.y + top,
        g.w - 4,
        g.h - top - 2,
      )) {
        const m = c.item;
        const b = bin(m);
        const rect = shape("rect", {
          x: c.x + 1,
          y: c.y + 1,
          width: Math.max(0, c.w - 2),
          height: Math.max(0, c.h - 2),
          rx: 3,
          fill: `var(${BINS[b].token})`,
          class: b === 0 ? "pbt-cell pbt-none" : "pbt-cell",
        });
        group.append(rect);
        const prefix = g.item.name === "fastmcp" ? "fastmcp." : `fastmcp.${g.item.name}.`;
        const short = m.module.startsWith(prefix)
          ? m.module.slice(prefix.length)
          : "__init__";
        if (c.w > 46 && c.h > 18)
          group.append(
            shape(
              "text",
              {
                x: c.x + 6,
                y: c.y + 15,
                class: b >= 3 ? "pbt-label pbt-label-inverse" : "pbt-label",
              },
              clip(short, c.w - 10, 6),
            ),
          );
        cells.push({ module: m, rect });
      }
    }
    const order = cells
      .map((c, i) => ({ i, name: c.module.module }))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((o) => o.i);
    let cursor = selected
      ? order.findIndex((i) => cells[i].module.module === selected)
      : -1;
    const show = (index) => {
      cells.forEach((c) => c.rect.classList.remove("pbt-active"));
      const c = cells[index];
      if (!c) return describe(null);
      c.rect.classList.add("pbt-active");
      describe(c.module);
    };
    const at = (event) => {
      return cells.findIndex((c) => c.rect === event.target);
    };
    graph.addEventListener("pointermove", (event) => {
      const index = at(event);
      if (index >= 0) show(index);
    });
    graph.addEventListener("pointerleave", () => {
      const pinned = cells.findIndex((c) => c.module.module === selected);
      show(pinned);
    });
    graph.addEventListener("click", (event) => {
      const index = at(event);
      if (index < 0) return;
      selected = cells[index].module.module;
      cursor = order.indexOf(index);
      remember(selected);
      show(index);
    });
    graph.addEventListener("focus", () => {
      cursor = Math.max(0, cursor);
      selected = cells[order[cursor]].module.module;
      show(order[cursor]);
    });
    graph.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      if (event.key === "ArrowLeft") cursor = Math.max(0, cursor - 1);
      if (event.key === "ArrowRight")
        cursor = Math.min(order.length - 1, cursor + 1);
      if (event.key === "Home") cursor = 0;
      if (event.key === "End") cursor = order.length - 1;
      selected = cells[order[cursor]].module.module;
      remember(selected);
      show(order[cursor]);
    });
    show(cells.findIndex((c) => c.module.module === selected));
    table(modules);
  }

  function clip(text, room, charWidth) {
    const fit = Math.floor(room / charWidth);
    if (text.length <= fit) return text;
    return fit > 2 ? `${text.slice(0, fit - 1)}…` : "";
  }

  function table(modules) {
    const host = el("pbt-table");
    host.replaceChildren();
    const t = make("table");
    const head = make("tr");
    for (const label of [
      "Module",
      "Lines reached",
      "Functions",
      "Tests",
      "Scope",
    ])
      head.append(make("th", label));
    t.append(head);
    for (const m of [...modules].sort(
      (a, b) => share(b) - share(a) || a.module.localeCompare(b.module),
    )) {
      const row = make("tr");
      const name = make("td");
      const link = make("a", m.module);
      link.href = moduleHash(m.module);
      name.append(link);
      row.append(
        name,
        make("td", `${pct(share(m))} (${m.hit}/${m.statements})`),
        make("td", `${m.functions_hit}/${m.functions}`),
        make("td", String(m.tests)),
        make("td", m.public ? "public" : "internal"),
      );
      t.append(row);
    }
    host.append(t);
  }

  function freshness() {
    const status = el("pbt-freshness");
    const at = new Date(report.generated_at).toLocaleString("en-US", {
      timeZone: "America/Chicago",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
    status.replaceChildren(`Measured ${at} CT`);
    if (report.source_commit) {
      const link = make("a", report.source_commit.slice(0, 9));
      link.href = COMMIT_URL + report.source_commit;
      status.append(" at ", link);
    }
    const files = report.suite.test_files.length;
    status.append(
      ` from ${files} property-based test file${files === 1 ? "" : "s"}.`,
    );
    const notice = el("pbt-notice");
    const parts = [];
    if (report.suite.status === "no_suite")
      parts.push(
        "No test on this commit imports hypothesis yet, so nothing is reached. The map fills in when property-based tests land.",
      );
    if (report.suite.status === "failed")
      parts.push(
        `The property-based suite failed (pytest exit ${report.suite.pytest_exit_code}). Coverage below is from the run as far as it got.`,
      );
    const age = (Date.now() - Date.parse(report.generated_at)) / 3600000;
    const stale = age > STALE_HOURS;
    if (stale)
      parts.push(
        `This was measured ${age < 48 ? `${Math.floor(age)} hours` : `${Math.floor(age / 24)} days`} ago, but the map rebuilds daily, so its rebuild is failing or not running.`,
      );
    notice.replaceChildren(...parts.map((p) => `${p} `));
    if (stale) {
      const runs = make("a", "See the rebuild runs.");
      runs.href = RUNS_URL;
      notice.append(runs);
    }
    notice.hidden = !parts.length;
  }

  async function load() {
    try {
      const response = await fetch(PBT_URL, { cache: "no-store" });
      if (!response.ok) throw new Error("unavailable");
      const next = await response.json();
      if (!valid(next)) throw new Error("invalid");
      const previous = report;
      report = next;
      try {
        freshness();
        if (previous || !follow()) draw();
      } catch (error) {
        report = previous;
        throw error;
      }
    } catch {
      if (!report) {
        el("pbt-freshness").textContent =
          "Property-based coverage data is unavailable.";
        return;
      }
      freshness();
      el("pbt-freshness").append(
        " Refresh failed; showing the last loaded data.",
      );
    }
  }
  function valid(next) {
    const moduleOk = (m) =>
      typeof m.module === "string" &&
      typeof m.path === "string" &&
      Number.isFinite(m.statements) &&
      Number.isFinite(m.hit) &&
      Array.isArray(m.symbols) &&
      Array.isArray(m.test_ids);
    return (
      next?.schema === "fastmcp-pbt-coverage/1" &&
      Number.isFinite(Date.parse(next.generated_at)) &&
      Array.isArray(next.suite?.test_files) &&
      Array.isArray(next.modules) &&
      next.modules.every(moduleOk)
    );
  }

  document.querySelectorAll("[data-pbt-scope]").forEach((button) =>
    button.addEventListener("click", () => {
      setScope(button.dataset.pbtScope);
      if (report) draw();
    }),
  );
  window.addEventListener("hashchange", () => linked() && follow());
  el("pbt-data").href = PBT_URL;
  legend();
  describe(null);
  let pending;
  new ResizeObserver(() => {
    clearTimeout(pending);
    pending = setTimeout(() => report && draw(), 100);
  }).observe(el("pbt-map"));
  load();
  // Follow the page's Refresh button and its five-minute poll.
  el("refresh").addEventListener("click", load);
  setInterval(load, 300000);
})();
