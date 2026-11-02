import * as amplitude from "@amplitude/analytics-browser";

// The same Amplitude setup as prefect.io and the FastMCP docs: one shared
// Prefect project, events sent through a same-origin proxy (/api/t) rather
// than api2.amplitude.com, and the device id forwarded on links to Prefect
// domains so a visitor stays one user across gofastmcp.com, prefect.io and
// the FastMCP Gateway app.
//
// The key is the public browser key the docs already ship in
// docs/fastmcp-analytics.js; it is safe in the client. Set
// NEXT_PUBLIC_AMPLITUDE_API_KEY to point a preview at another project.
const DEFAULT_API_KEY = "c361ed56e7bdc1a48a38773c40120b39";
export const AMPLITUDE_API_KEY = process.env.NEXT_PUBLIC_AMPLITUDE_API_KEY?.trim() || DEFAULT_API_KEY;

export const SOURCE = "fastmcp-website";
export const SOURCE_DETAIL = "fastmcp";
export const SURFACE = "fastmcp_website";
export const PAGE_VIEW_EVENT = "Page View: FastMCP Website";

const COMMON_PROPERTIES = { source: SOURCE, source_detail: SOURCE_DETAIL, surface: SURFACE };

// Hosts that share the Amplitude project. Links to them carry the device id
// (the same three parameter names prefect.io decorates with, so either side
// can read it).
const PREFECT_HOSTNAMES = new Set([
  "prefect.io",
  "www.prefect.io",
  "horizon.prefect.io",
  "app.prefect.cloud",
  "dagster.io",
  "www.dagster.io",
]);
const DEVICE_ID_PARAMS = ["amp_device_id", "ampDeviceId", "deviceId"] as const;

let initialized = false;

export function isPrefectDestination(url: URL) {
  return PREFECT_HOSTNAMES.has(url.hostname);
}

// Analytics stay off in local development, and for visitors who ask not to
// be tracked through Global Privacy Control or Do Not Track.
export function analyticsEnabled() {
  if (typeof window === "undefined") return false;
  if (process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_ANALYTICS_IN_DEV !== "1") {
    return false;
  }
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean };
  if (nav.globalPrivacyControl || nav.doNotTrack === "1") return false;
  return true;
}

export function init() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;

  amplitude.add({
    name: "fastmcp-website-properties",
    type: "enrichment",
    execute: async (event) => ({
      ...event,
      event_properties: { ...COMMON_PROPERTIES, ...event.event_properties },
    }),
  });

  amplitude.init(AMPLITUDE_API_KEY, undefined, {
    useBatch: false,
    serverUrl: new URL("/api/t", window.location.origin).toString(),
    // Autocapture's remote config is fetched through our domain too.
    remoteConfig: {
      serverUrl: new URL("/api/sr-cfg", window.location.origin).toString(),
    },
    autocapture: {
      elementInteractions: true,
      formInteractions: false,
      // Adds [Amplitude] Page Domain / URL / Path to every event; page views
      // themselves are the custom event below so they match the docs' shape.
      pageViews: false,
      pageUrlEnrichment: true,
    },
    // @ts-expect-error - attribution config exists but not in types
    attribution: {
      disabled: false,
      trackNewCampaigns: true,
      trackPageViews: false,
      resetSessionOnNewCampaign: true,
    },
  });
}

export const track = amplitude.track;

export function trackPageView(path: string) {
  amplitude.track(PAGE_VIEW_EVENT, {
    path,
    url: window.location.href,
    title: document.title,
    referrer: document.referrer || null,
  });
}

export interface CtaClickedProperties {
  cta_name: string;
  cta_text: string;
  cta_destination: string;
  page_path: string;
  page_section: string;
  cta_variant?: string;
}

export function trackCtaClicked(properties: CtaClickedProperties) {
  amplitude.track("cta_clicked", properties);
}

export function trackOutboundClick(link: HTMLAnchorElement, destination: URL, section?: string) {
  amplitude.track("Website Outbound Clicked", {
    path: window.location.pathname,
    url: window.location.href,
    title: document.title,
    destination: destination.href,
    destination_domain: destination.hostname,
    link_text: (link.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 200),
    page_section: section,
    is_prefect_destination: isPrefectDestination(destination),
  });
}

export function trackDocsLinkClicked(link: HTMLAnchorElement, destination: URL, section?: string) {
  amplitude.track("Docs Link Clicked", {
    path: window.location.pathname,
    destination: destination.pathname,
    link_text: (link.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 200),
    page_section: section,
  });
}

// Stamps the device id onto a Prefect link right before it is followed, so
// the id is current and the decorated URL never sits in the DOM for long.
export function decorateWithDeviceId(link: HTMLAnchorElement) {
  try {
    const url = new URL(link.href);
    if (!isPrefectDestination(url)) return;
    const deviceId = amplitude.getDeviceId();
    if (!deviceId) return;
    for (const key of DEVICE_ID_PARAMS) url.searchParams.set(key, deviceId);
    link.href = url.toString();
  } catch {
    // A malformed href is left alone.
  }
}
