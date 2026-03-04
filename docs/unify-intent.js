// Load Unify intent tag on authentication pages only
(function () {
  if (typeof window === "undefined") return;

  function isAuthPage() {
    var path = window.location.pathname;
    return path.includes("/servers/auth/") || path.includes("/clients/auth/");
  }

  function loadUnify() {
    var e = [
      "identify",
      "page",
      "startAutoPage",
      "stopAutoPage",
      "startAutoIdentify",
      "stopAutoIdentify",
    ];
    function t(o) {
      return Object.assign(
        [],
        e.reduce(function (r, n) {
          r[n] = function () {
            return o.push([n, [].slice.call(arguments)]), o;
          };
          return r;
        }, {}),
      );
    }
    if (!window.unify) window.unify = t(window.unify);
    if (!window.unifyBrowser) window.unifyBrowser = t(window.unifyBrowser);

    var n = document.createElement("script");
    n.async = true;
    n.setAttribute(
      "src",
      "https://tag.unifyintent.com/v1/Rj9KrQqMhyYcU5qfJtVszE/script.js",
    );
    n.setAttribute(
      "data-api-key",
      "wk_SBvJ4jyD_wRgPAHCNJb89seVmREhcj2NspRpxAywi",
    );
    n.setAttribute("id", "unifytag");
    (document.body || document.head).appendChild(n);
  }

  function update() {
    if (isAuthPage() && !document.getElementById("unifytag")) {
      loadUnify();
    } else if (!isAuthPage() && document.getElementById("unifytag")) {
      document.getElementById("unifytag").remove();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", update);
  } else {
    update();
  }

  var lastUrl = location.href;
  new MutationObserver(function () {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      setTimeout(update, 100);
    }
  }).observe(document.body, { subtree: true, childList: true });
})();
