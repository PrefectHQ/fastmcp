(function () {
  if (typeof window === "undefined") return;

  // Public browser key for the shared Prefect Amplitude project.
  // This is intentionally client-side; the secret key must never ship to the browser.
  var AMPLITUDE_API_KEY = "c361ed56e7bdc1a48a38773c40120b39";
  var AMPLITUDE_SCRIPT_URL =
    "https://cdn.amplitude.com/libs/analytics-browser-2.8.1-min.js.gz";
  var AMPLITUDE_SERVER_URL = "https://api2.amplitude.com/2/httpapi";
  var PAGE_VIEW_EVENT = "Page View: FastMCP Docs";
  var OUTBOUND_CLICK_EVENT = "Docs Outbound Clicked";
  var SOURCE = "docs";
  var SOURCE_DETAIL = "fastmcp";
  var SURFACE = "fastmcp_docs";
  var DEVICE_ID_PARAM = "deviceId";
  var routeListenersInstalled = false;
  var amplitudeInitialized = false;
  var lastTrackedUrl = null;

  var PREFECT_DESTINATION_HOSTNAMES = [
    "www.prefect.io",
    "prefect.io",
    "horizon.prefect.io",
    "app.prefect.cloud",
  ];

  var routeChangeCallbacks = [];

  function loadScript(src, onload) {
    var script = document.createElement("script");
    script.src = src;
    script.async = true;

    if (typeof onload === "function") {
      script.addEventListener("load", onload);
    }

    document.head.appendChild(script);
    return script;
  }

  function getAmplitude() {
    return window.amplitude || window.amplitudeAnalytics;
  }

  function normalizePathname(pathname) {
    if (pathname === "/") return pathname;
    return pathname.replace(/\/+$/, "");
  }

  function observeRouteChanges(callback) {
    routeChangeCallbacks.push(callback);

    if (!routeListenersInstalled) {
      var fireCallbacks = function () {
        routeChangeCallbacks.forEach(function (cb) {
          window.setTimeout(cb, 0);
        });
      };

      var wrapHistoryMethod = function (methodName) {
        var original = window.history[methodName];
        window.history[methodName] = function () {
          var result = original.apply(this, arguments);
          fireCallbacks();
          return result;
        };
      };

      wrapHistoryMethod("pushState");
      wrapHistoryMethod("replaceState");
      window.addEventListener("popstate", fireCallbacks);
      window.addEventListener("hashchange", fireCallbacks);
      routeListenersInstalled = true;
    }

    callback();
  }

  function buildPageViewProperties() {
    return {
      url: window.location.href,
      title: document.title,
      referrer: document.referrer || null,
      path: normalizePathname(window.location.pathname),
      source: SOURCE,
      source_detail: SOURCE_DETAIL,
      surface: SURFACE,
    };
  }

  function trackPageView() {
    var amplitude = getAmplitude();
    if (!amplitude || typeof amplitude.track !== "function") {
      return;
    }

    var url = window.location.href;
    if (url === lastTrackedUrl) {
      return;
    }

    amplitude.track(PAGE_VIEW_EVENT, buildPageViewProperties());
    lastTrackedUrl = url;
  }

  function parseUrl(href) {
    try {
      return new URL(href, window.location.origin);
    } catch (error) {
      return null;
    }
  }

  function isPrefectDestination(url) {
    return PREFECT_DESTINATION_HOSTNAMES.indexOf(url.hostname) !== -1;
  }

  function addDeviceIdToLink(event) {
    var amplitude = getAmplitude();
    if (!amplitude || typeof amplitude.getDeviceId !== "function") {
      return;
    }

    var link = event.currentTarget;
    var href = link.getAttribute("href") || "";

    var url = parseUrl(href);
    if (!url || !isPrefectDestination(url)) {
      return;
    }

    url.searchParams.set(DEVICE_ID_PARAM, amplitude.getDeviceId());
    link.href = url.toString();
  }

  function removeDeviceIdFromLink(event) {
    var link = event.currentTarget;
    var href = link.getAttribute("href") || "";

    var url = parseUrl(href);
    if (!url || !isPrefectDestination(url)) {
      return;
    }

    url.searchParams.delete(DEVICE_ID_PARAM);
    link.href = url.toString();
  }

  function attachDeviceIdForwarding() {
    var elements = document.querySelectorAll("a[href]");
    elements.forEach(function (element) {
      if (element.dataset.fastmcpDeviceIdBound === "true") {
        return;
      }

      var url = parseUrl(element.getAttribute("href") || "");
      if (!url || !isPrefectDestination(url)) {
        return;
      }

      element.addEventListener("mouseenter", addDeviceIdToLink);
      element.addEventListener("mouseleave", removeDeviceIdFromLink);
      element.addEventListener("focus", addDeviceIdToLink);
      element.addEventListener("blur", removeDeviceIdFromLink);
      element.addEventListener("touchstart", addDeviceIdToLink);
      element.addEventListener("touchcancel", removeDeviceIdFromLink);
      element.dataset.fastmcpDeviceIdBound = "true";
    });
  }

  function trackOutboundClick(event) {
    var link = event.target && event.target.closest
      ? event.target.closest("a[href]")
      : null;

    if (!link) {
      return;
    }

    var href = link.getAttribute("href");
    if (!href || href[0] === "#") {
      return;
    }

    var destination;
    destination = parseUrl(href);
    if (!destination) {
      return;
    }

    if (destination.hostname === window.location.hostname) {
      return;
    }

    var amplitude = getAmplitude();
    if (!amplitude || typeof amplitude.track !== "function") {
      return;
    }

    amplitude.track(OUTBOUND_CLICK_EVENT, {
      path: normalizePathname(window.location.pathname),
      url: window.location.href,
      title: document.title,
      source: SOURCE,
      source_detail: SOURCE_DETAIL,
      surface: SURFACE,
      destination: destination.href,
      destination_domain: destination.hostname,
      link_text: (link.textContent || "").trim().slice(0, 200),
      is_prefect_destination: isPrefectDestination(destination),
    });
  }

  function initializeAmplitude() {
    var amplitude = getAmplitude();
    if (
      amplitudeInitialized ||
      !amplitude ||
      typeof amplitude.init !== "function"
    ) {
      return;
    }

    amplitude.init(AMPLITUDE_API_KEY, undefined, {
      useBatch: true,
      serverUrl: AMPLITUDE_SERVER_URL,
      attribution: {
        disabled: false,
        trackNewCampaigns: true,
        trackPageViews: true,
        resetSessionOnNewCampaign: true,
      },
      defaultTracking: {
        pageViews: false,
        sessions: false,
        formInteractions: true,
        fileDownloads: true,
      },
    });

    amplitudeInitialized = true;
    observeRouteChanges(trackPageView);
    observeRouteChanges(attachDeviceIdForwarding);
  }

  function initialize() {
    document.addEventListener("click", trackOutboundClick, true);
    loadScript(AMPLITUDE_SCRIPT_URL, initializeAmplitude);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
