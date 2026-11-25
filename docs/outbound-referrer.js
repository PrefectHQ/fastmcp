// Mintlify renders every external link with rel="noreferrer", so prefect.io and
// other destinations see docs traffic as "direct". Dropping that token right
// before the browser follows the link lets the Referer header through; the
// site's Referrer-Policy header still limits what is sent (origin only).
(function () {
  if (typeof window === "undefined") return;

  var EVENTS = ["pointerdown", "mousedown", "click", "auxclick", "keydown"];

  function allowReferrer(event) {
    var target = event.target;
    if (!target || typeof target.closest !== "function") {
      return;
    }

    var link = target.closest('a[rel~="noreferrer"]');
    if (!link) {
      return;
    }

    var tokens = (link.getAttribute("rel") || "").split(/\s+/).filter(Boolean);
    tokens = tokens.filter(function (token) {
      return token !== "noreferrer";
    });

    if (tokens.indexOf("noopener") === -1) {
      tokens.push("noopener");
    }

    link.setAttribute("rel", tokens.join(" "));
  }

  EVENTS.forEach(function (eventName) {
    document.addEventListener(eventName, allowReferrer, true);
  });
})();
