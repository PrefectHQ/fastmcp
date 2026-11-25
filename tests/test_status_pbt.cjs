// The coverage map's links and freshness warning, run against a fake DOM.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function element() {
  const node = {
    children: [],
    attributes: {},
    listeners: {},
    dataset: {},
    style: {},
    classList: { add() {}, remove() {} },
    clientWidth: 800,
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    addEventListener(type, listener) {
      (this.listeners[type] ||= []).push(listener);
    },
  };
  return node;
}

function harness({ hash = "", report }) {
  const elements = new Map();
  const buttons = ["public", "all"].map((scope) => {
    const button = element();
    button.dataset.pbtScope = scope;
    return button;
  });
  const win = element();
  win.location = {
    origin: "http://localhost",
    href: `http://localhost/${hash}`,
    hash,
  };
  const context = vm.createContext({
    URL,
    Date,
    console,
    window: win,
    history: {
      replaceState(_state, _title, url) {
        win.location.hash = url;
      },
    },
    navigator: {},
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      createElement: element,
      createElementNS: (_ns, _tag) => element(),
      querySelectorAll: (selector) =>
        selector === "[data-pbt-scope]" ? buttons : [],
    },
    ResizeObserver: class {
      observe() {}
    },
    setInterval() {},
    setTimeout,
    clearTimeout,
    fetch: async () => ({ ok: true, json: async () => report }),
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../status-site/pbt.js"), "utf8"),
    context,
  );
  return { elements, buttons, win };
}

const coverageModule = (name, isPublic) => ({
  module: name,
  path: `fastmcp_slim/${name.replaceAll(".", "/")}.py`,
  statements: 10,
  hit: isPublic ? 5 : 0,
  functions: 2,
  functions_hit: isPublic ? 1 : 0,
  tests: isPublic ? 1 : 0,
  public: isPublic,
  symbols: [],
  test_ids: [],
});
const report = (hoursAgo) => ({
  schema: "fastmcp-pbt-coverage/1",
  generated_at: new Date(Date.now() - hoursAgo * 3600000).toISOString(),
  source_commit: "0123456789abcdef",
  suite: { status: "passed", test_files: ["tests/test_x.py"], tests_reaching_fastmcp: 1 },
  modules: [
    coverageModule("fastmcp.server.server", true),
    coverageModule("fastmcp.cli.cli", false),
  ],
});
const flush = () => new Promise((resolve) => setImmediate(resolve));
const text = (node) =>
  typeof node === "string"
    ? node
    : (node.textContent ?? "") + node.children.map(text).join("");
const selectedName = (h) =>
  h.elements.get("pbt-readout").children[0]?.children[0]?.textContent;

test("fresh data shows no warning", async () => {
  const h = harness({ report: report(2) });
  await flush();
  assert.equal(h.elements.get("pbt-notice").hidden, true);
});

test("data older than a day and a half says the rebuild is failing", async () => {
  const h = harness({ report: report(72) });
  await flush();
  const notice = h.elements.get("pbt-notice");
  assert.equal(notice.hidden, false);
  assert.match(text(notice), /measured 3 days ago/);
  assert.match(text(notice), /See the rebuild runs/);
});

test("a module link selects it and widens the map for internal modules", async () => {
  const h = harness({
    hash: "#pbt-coverage/fastmcp.cli.cli",
    report: report(1),
  });
  await flush();
  assert.equal(selectedName(h), "fastmcp.cli.cli");
  assert.equal(h.buttons[1].attributes["aria-pressed"], "true");
  assert.equal(h.buttons[0].attributes["aria-pressed"], "false");
});

test("a later hash change follows the new module", async () => {
  const h = harness({ report: report(1) });
  await flush();
  assert.equal(selectedName(h), undefined);
  h.win.location.hash = "#pbt-coverage/fastmcp.server.server";
  for (const listener of h.win.listeners.hashchange) listener();
  assert.equal(selectedName(h), "fastmcp.server.server");
});

for (const hash of ["#pbt-coverage/%", "#pbt-coverage/fastmcp.nope"]) {
  test(`an unusable link (${hash}) still draws the map`, async () => {
    const h = harness({ hash, report: report(1) });
    await flush();
    assert.match(text(h.elements.get("pbt-freshness")), /^Measured/);
    assert.ok(h.elements.get("pbt-map").children.length > 0);
    assert.equal(selectedName(h), undefined);
  });
}
