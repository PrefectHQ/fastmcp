// Section links. The charts above a linked section draw after their data
// arrives, so the browser's own jump lands too high; keep the target aligned
// while the page settles, and stop as soon as the reader scrolls themselves.
// A hash like #pbt-coverage/fastmcp.server.proxy aligns to its section;
// pbt.js selects the module.
(() => {
  const target = () => {
    const hash = decodeURIComponent(window.location.hash.slice(1));
    if (!hash) return null;
    return (
      document.getElementById(hash) ||
      document.getElementById(hash.split("/")[0])
    );
  };
  let settling = true;
  const align = () => {
    const node = settling && target();
    if (node) node.scrollIntoView({ block: "start" });
  };
  const stop = () => {
    settling = false;
    observer.disconnect();
  };
  const observer = new ResizeObserver(align);
  observer.observe(document.querySelector("main"));
  for (const event of ["wheel", "touchstart", "keydown", "pointerdown"])
    window.addEventListener(event, stop, { once: true, passive: true });
  setTimeout(stop, 8000);
  // A section link clicked later (or a pasted hash) needs the same treatment
  // only if it points into the deep-link form the browser can't resolve.
  window.addEventListener("hashchange", () => {
    const hash = decodeURIComponent(window.location.hash.slice(1));
    if (hash && !document.getElementById(hash)) target()?.scrollIntoView();
  });
})();
