"use strict";

const topology = {
  schema_version: 1,
  title: "fastmcp-triage",
  version: "06ae2206-1ef8-76f7-8000-ac285aca5f8f",
  semantic_hash:
    "eff29f8da9156022795ec29ef43b8a687a12241d49a75c953394793de3094473",
  inputs: ["parameters", "assess_request", "reproduce_request"],
  nodes: [
    {
      id: "__plan_inputs__",
      kind: "Inputs",
      label: "Plan inputs",
      inputs: [],
      outputs: ["parameters", "assess_request", "reproduce_request"],
      x: 12,
      y: 77.00000000000003,
      width: 215,
      height: 192,
    },
    {
      id: "assess",
      label: "Assess issue · Luna",
      kind: "AgentNode",
      inputs: ["request"],
      outputs: ["verify", "reproduce", "record", "blocked"],
      selection: "exactly_one",
      model: {
        id: "gpt-6-luna",
        effort: "low",
      },
      x: 327,
      y: 257.33333333333337,
      width: 300,
      height: 220,
    },
    {
      id: "verify",
      label: "Verify existing contributions · deterministic",
      kind: "DeploymentNode",
      inputs: ["parameters", "assessment"],
      outputs: ["completed", "failed", "crashed", "cancelled", "timed_out"],
      selection: "exactly_one",
      x: 727,
      y: 136.00000000000003,
      width: 300,
      height: 248,
    },
    {
      id: "reproduce",
      label: "Reproduce bug · Luna",
      kind: "AgentNode",
      inputs: ["request", "assessment"],
      outputs: ["result"],
      selection: "exactly_one",
      model: {
        id: "gpt-6-luna",
        effort: "medium",
      },
      x: 727,
      y: 432.66666666666674,
      width: 300,
      height: 164,
    },
    {
      id: "finalize",
      label: "Retain decision and evidence · deterministic",
      kind: "DeploymentNode",
      inputs: ["parameters", "outcomes"],
      outputs: ["completed", "failed", "crashed", "cancelled", "timed_out"],
      selection: "exactly_one",
      x: 1127,
      y: 12,
      width: 300,
      height: 248,
    },
  ],
  edges: [
    {
      id: "edge_0",
      from: {
        type: "plan_input",
        input: "assess_request",
      },
      to: {
        node: "assess",
        input: "request",
      },
    },
    {
      id: "edge_1",
      from: {
        type: "plan_input",
        input: "reproduce_request",
      },
      to: {
        node: "reproduce",
        input: "request",
      },
    },
    {
      id: "edge_2",
      from: {
        type: "plan_input",
        input: "parameters",
      },
      to: {
        node: "verify",
        input: "parameters",
      },
    },
    {
      id: "edge_3",
      from: {
        type: "plan_input",
        input: "parameters",
      },
      to: {
        node: "finalize",
        input: "parameters",
      },
    },
    {
      id: "edge_4",
      from: {
        type: "node_output",
        node: "assess",
        output: "verify",
      },
      to: {
        node: "verify",
        input: "assessment",
      },
    },
    {
      id: "edge_5",
      from: {
        type: "node_output",
        node: "assess",
        output: "verify",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_6",
      from: {
        type: "node_output",
        node: "assess",
        output: "reproduce",
      },
      to: {
        node: "reproduce",
        input: "assessment",
      },
    },
    {
      id: "edge_7",
      from: {
        type: "node_output",
        node: "assess",
        output: "reproduce",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_8",
      from: {
        type: "node_output",
        node: "assess",
        output: "record",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_9",
      from: {
        type: "node_output",
        node: "assess",
        output: "blocked",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_10",
      from: {
        type: "node_output",
        node: "reproduce",
        output: "result",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_11",
      from: {
        type: "node_output",
        node: "verify",
        output: "completed",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_12",
      from: {
        type: "node_output",
        node: "verify",
        output: "failed",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_13",
      from: {
        type: "node_output",
        node: "verify",
        output: "crashed",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_14",
      from: {
        type: "node_output",
        node: "verify",
        output: "cancelled",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
    {
      id: "edge_15",
      from: {
        type: "node_output",
        node: "verify",
        output: "timed_out",
      },
      to: {
        node: "finalize",
        input: "outcomes",
      },
    },
  ],
  width: 1439,
  height: 619.6666666666667,
};

function svgElement(tag, attributes = {}, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes))
    element.setAttribute(name, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}

function textElement(parent, x, y, text, className, extra = {}) {
  parent.append(svgElement("text", { x, y, class: className, ...extra }, text));
}

function initializeGraph() {
  const svg = document.querySelector("#graph");
  const canvas = document.querySelector("#canvas");
  const descriptions = {
    __plan_inputs__:
      "The controller supplies parameters and two requests. These are data bindings, not three independently scheduled jobs. A request alone does not activate a branch: its assessment input must also arrive.",
    assess:
      "Luna reads the issue, contribution history, ownership, holds, and pinned repository skills. It selects exactly one output: verify an existing contribution, reproduce a defect, record a decision, or report blocked work. Every selection also feeds the finalizer.",
    verify:
      "A deterministic deployment checks selected contributions against baseline and PR revisions. Each terminal output is wired to the finalizer, including failed, crashed, cancelled, and timed_out. Failure is retained as evidence rather than disappearing from the graph.",
    reproduce:
      "Luna runs a bounded probe on the pinned source and returns its result plus any regression-only patch. This node reproduces the bug; it does not prepare or publish a fix.",
    finalize:
      "A deterministic deployment joins the selected assessment with any verification or reproduction result and retains the outcome. Its completed output supplies the plan result. Independent triage review, patch preparation, GitHub writes, and final intake freshness happen in the outer Python controller.",
  };
  const titles = {
    __plan_inputs__: "Plan inputs",
    assess: "Assess issue",
    verify: "Verify existing contributions",
    reproduce: "Reproduce bug",
    finalize: "Retain decision and evidence",
  };
  const defs = svgElement("defs");
  const marker = svgElement("marker", {
    id: "arrow",
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto-start-reverse",
  });
  marker.append(
    svgElement("path", {
      d: "M 0 1 L 9 5 L 0 9",
      fill: "none",
      stroke: "#939aaa",
      "stroke-width": 1.5,
    }),
  );
  defs.append(marker);
  svg.append(defs);
  const scene = svgElement("g", { id: "scene" });
  svg.append(scene);
  const edgeLayer = svgElement("g");
  const nodeLayer = svgElement("g");
  scene.append(edgeLayer, nodeLayer);
  const nodes = new Map(topology.nodes.map((n) => [n.id, n]));
  const portY = (node, port, output) =>
    node.y + 103 + (output ? node.outputs : node.inputs).indexOf(port) * 28;
  const paths = new Map();
  for (const edge of topology.edges) {
    const from = nodes.get(
      edge.from.type === "plan_input" ? "__plan_inputs__" : edge.from.node,
    );
    const to = nodes.get(edge.to.node);
    const x1 = from.x + from.width,
      y1 = portY(from, edge.from.output || edge.from.input, true),
      x2 = to.x,
      y2 = portY(to, edge.to.input, false);
    const bend = (x1 + x2) / 2;
    const path = svgElement("path", {
      class: "edge" + (edge.from.type === "plan_input" ? " input-edge" : ""),
      d: `M${x1},${y1} C${bend},${y1} ${bend},${y2} ${x2},${y2}`,
      "marker-end": "url(#arrow)",
      "data-edge": edge.id,
    });
    path.append(
      svgElement(
        "title",
        {},
        `${from.id}.${edge.from.output || edge.from.input} → ${to.id}.${edge.to.input}`,
      ),
    );
    edgeLayer.append(path);
    paths.set(edge.id, path);
  }
  function inspect(id) {
    document
      .querySelectorAll(".node")
      .forEach((n) => n.classList.toggle("selected", n.dataset.node === id));
    const inspector = document.querySelector("#inspector");
    inspector.replaceChildren();
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = titles[id];
    const code = document.createElement("code");
    code.textContent = id === "__plan_inputs__" ? "plan.inputs" : `nodes.${id}`;
    heading.append(title, code);
    const explanation = document.createElement("p");
    explanation.textContent = descriptions[id];
    inspector.append(heading, explanation);
  }
  for (const node of topology.nodes) {
    const group = svgElement("g", {
      class: "node",
      transform: `translate(${node.x} ${node.y})`,
      tabindex: 0,
      role: "button",
      "aria-label": `Inspect ${titles[node.id]}`,
      "data-node": node.id,
    });
    const deployment = node.kind === "DeploymentNode";
    const color = deployment
      ? "#2dd4bf"
      : node.kind === "Inputs"
        ? "#9ca3b4"
        : "#b39af3";
    if (deployment)
      group.append(
        svgElement("rect", {
          x: 5,
          y: -5,
          width: node.width,
          height: node.height,
          rx: 8,
          class: "stack-back",
        }),
      );
    group.append(
      svgElement("rect", {
        width: node.width,
        height: node.height,
        rx: 8,
        class: "card" + (node.kind === "Inputs" ? " input-card" : ""),
      }),
    );
    group.append(
      svgElement("rect", {
        x: 16,
        y: 16,
        width: 21,
        height: 21,
        rx: 5,
        fill: deployment
          ? "#163f39"
          : node.kind === "Inputs"
            ? "#303541"
            : "#3d2d5d",
      }),
    );
    textElement(
      group,
      26,
      31,
      deployment ? "↗" : node.kind === "Inputs" ? "→" : "◇",
      "kind",
      { fill: color, "text-anchor": "middle" },
    );
    textElement(
      group,
      45,
      30,
      deployment
        ? "DEPLOYMENT"
        : node.kind === "Inputs"
          ? "PLAN INPUTS"
          : "AGENT",
      "kind",
      { fill: color },
    );
    if (node.outputs.length > 1 && node.selection === "exactly_one")
      textElement(
        group,
        node.width - 14,
        30,
        `1 of ${node.outputs.length}`,
        "subtitle",
        { "text-anchor": "end" },
      );
    textElement(group, 16, 60, titles[node.id], "title");
    textElement(
      group,
      16,
      80,
      node.model
        ? `${node.model.id} · ${node.model.effort}`
        : deployment
          ? "Deterministic worker"
          : "Controller-supplied values",
      "subtitle",
    );
    for (const [output, ports] of [
      [false, node.inputs],
      [true, node.outputs],
    ]) {
      ports.forEach((port, i) => {
        const y = 103 + i * 28;
        const width =
          node.inputs.length && node.outputs.length
            ? node.width / 2 - 22
            : node.width - 32;
        const x = output ? node.width - width - 16 : 16;
        group.append(
          svgElement("rect", {
            x,
            y: y - 11,
            width,
            height: 22,
            rx: 4,
            class: "port-bg",
          }),
        );
        textElement(
          group,
          output ? node.width - 22 : 22,
          y + 4,
          port,
          "port-label",
          { "text-anchor": output ? "end" : "start" },
        );
        const cx = output ? node.width : 0;
        if (output && node.selection === "exactly_one" && ports.length > 1)
          group.append(
            svgElement("path", {
              d: `M${cx},${y - 4} l4,4 -4,4 -4,-4 Z`,
              class: "port branch-port",
            }),
          );
        else
          group.append(
            svgElement("circle", { cx, cy: y, r: 3.5, class: "port" }),
          );
      });
    }
    group.addEventListener("click", () => inspect(node.id));
    group.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        inspect(node.id);
      }
    });
    nodeLayer.append(group);
  }
  function branch(name) {
    document
      .querySelectorAll("[data-branch]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.branch === name)),
      );
    const allowedNodes = new Set([
      "__plan_inputs__",
      "assess",
      "finalize",
      ...(name === "verify"
        ? ["verify"]
        : name === "reproduce"
          ? ["reproduce"]
          : []),
    ]);
    for (const e of topology.edges) {
      const active =
        name === "all" ||
        (e.from.type === "plan_input"
          ? allowedNodes.has(e.to.node)
          : e.from.node === "assess"
            ? e.from.output === name
            : e.from.node === name);
      paths.get(e.id).classList.toggle("muted", !active);
      paths
        .get(e.id)
        .classList.toggle(
          "active",
          name !== "all" && active && e.from.type !== "plan_input",
        );
    }
    nodeLayer
      .querySelectorAll(".node")
      .forEach((n) =>
        n.classList.toggle(
          "muted",
          name !== "all" && !allowedNodes.has(n.dataset.node),
        ),
      );
  }
  document
    .querySelectorAll("[data-branch]")
    .forEach((b) =>
      b.addEventListener("click", () => branch(b.dataset.branch)),
    );
  let view = {
    x: -35,
    y: -35,
    width: topology.width + 70,
    height: topology.height + 70,
  };
  function applyView() {
    svg.setAttribute(
      "viewBox",
      `${view.x} ${view.y} ${view.width} ${view.height}`,
    );
  }
  function fit() {
    view = {
      x: -35,
      y: -35,
      width: topology.width + 70,
      height: topology.height + 70,
    };
    applyView();
  }
  const expand = document.querySelector("#expand");
  function toggleExpanded() {
    const expanded = document
      .querySelector(".workspace")
      .classList.toggle("expanded");
    expand.setAttribute("aria-pressed", String(expanded));
    expand.textContent = expanded ? "Close" : "Expand";
    fit();
  }
  expand.addEventListener("click", toggleExpanded);
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      expand.getAttribute("aria-pressed") === "true"
    )
      toggleExpanded();
  });
  function zoom(factor) {
    const width = Math.min(
      (topology.width + 70) * 2,
      Math.max(280, view.width * factor),
    );
    const scale = width / view.width;
    view = {
      x: view.x + (view.width - width) / 2,
      y: view.y + (view.height - view.height * scale) / 2,
      width,
      height: view.height * scale,
    };
    applyView();
  }
  document.querySelector("#zoom-in").addEventListener("click", () => zoom(0.8));
  document
    .querySelector("#zoom-out")
    .addEventListener("click", () => zoom(1.25));
  document.querySelector("#fit").addEventListener("click", fit);
  svg.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      zoom(event.deltaY > 0 ? 1.1 : 0.9);
    },
    { passive: false },
  );
  let drag = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".node")) return;
    drag = { x: event.clientX, y: event.clientY, view: { ...view } };
    svg.setPointerCapture(event.pointerId);
    canvas.classList.add("dragging");
  });
  svg.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const scale = Math.max(
      view.width / svg.clientWidth,
      view.height / svg.clientHeight,
    );
    view.x = drag.view.x - (event.clientX - drag.x) * scale;
    view.y = drag.view.y - (event.clientY - drag.y) * scale;
    applyView();
  });
  const endDrag = () => {
    drag = null;
    canvas.classList.remove("dragging");
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("keydown", (event) => {
    if (event.target !== canvas) return;
    if (["+", "=", "-", "0"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "0") fit();
      else zoom(event.key === "-" ? 1.25 : 0.8);
    }
  });
  function download(name, type, content) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  document
    .querySelector("#export-json")
    .addEventListener("click", () =>
      download(
        "fastmcp-triage-topology.json",
        "application/json",
        JSON.stringify(topology, null, 2),
      ),
    );
  document.querySelector("#export-svg").addEventListener("click", () => {
    const copy = svg.cloneNode(true);
    copy.setAttribute(
      "viewBox",
      `-35 -35 ${topology.width + 70} ${topology.height + 70}`,
    );
    copy.setAttribute("width", topology.width + 70);
    copy.setAttribute("height", topology.height + 70);
    const allElements = [svg, ...svg.querySelectorAll("*")];
    const copied = [copy, ...copy.querySelectorAll("*")];
    for (let i = 0; i < allElements.length; i++) {
      const computed = getComputedStyle(allElements[i]);
      for (const prop of [
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "opacity",
        "font-family",
        "font-size",
        "font-weight",
        "letter-spacing",
        "text-anchor",
      ]) {
        copied[i].style.setProperty(prop, computed.getPropertyValue(prop));
      }
    }
    const background = svgElement("rect", {
      x: -35,
      y: -35,
      width: topology.width + 70,
      height: topology.height + 70,
      fill: "#101114",
    });
    copy.insertBefore(background, copy.firstChild);
    download(
      "fastmcp-triage-graph.svg",
      "image/svg+xml",
      new XMLSerializer().serializeToString(copy),
    );
  });
  fit();
  inspect("assess");
}

initializeGraph();
