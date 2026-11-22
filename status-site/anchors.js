// Section links. The charts above a linked section draw after their data
// arrives, so the browser's own jump lands too high; keep the target aligned
// while the page settles, and stop as soon as the reader scrolls themselves.
// A hash like #pbt-coverage/fastmcp.server.proxy aligns to its section;
// pbt.js selects the module.
(() => {
  // A hand-typed hash can hold a broken escape; read it raw rather than fail.
  const hash = () => {
    const raw = window.location.hash.slice(1);
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  };
  const target = () => {
    const h = hash();
    if (!h) return null;
    return document.getElementById(h) || document.getElementById(h.split("/")[0]);
  };
  // A reload or back/forward restores the reader's own position; leave it.
  const nav = performance.getEntriesByType?.("navigation")[0];
  let settling = !nav || nav.type === "navigate";
  let expected = null;
  const align = () => {
    const node = settling && target();
    if (!node) return;
    node.scrollIntoView({ block: "start" });
    expected = window.scrollY;
  };
  const observer = new ResizeObserver(align);
  const stop = () => {
    settling = false;
    observer.disconnect();
  };
  if (settling) {
    observer.observe(document.querySelector("main"));
    for (const event of ["wheel", "touchstart", "keydown", "pointerdown"])
      window.addEventListener(event, stop, { once: true, passive: true });
    // Scrollbar drags send none of the above; any scroll we didn't make ends it.
    window.addEventListener(
      "scroll",
      () => {
        if (expected !== null && Math.abs(window.scrollY - expected) > 4) stop();
      },
      { passive: true },
    );
    setTimeout(stop, 8000);
  }
  // The browser can't resolve the module form on its own.
  window.addEventListener("hashchange", () => {
    const h = hash();
    if (h && !document.getElementById(h)) target()?.scrollIntoView();
  });
})();
