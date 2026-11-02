"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";
import {
  analyticsEnabled,
  decorateWithDeviceId,
  init,
  trackCtaClicked,
  trackDocsLinkClicked,
  trackOutboundClick,
  trackPageView,
} from "@/lib/analytics/amplitude";

const CTA_SELECTOR = '[data-analytics-event="cta_clicked"]';

function sectionOf(element: Element) {
  return element.closest<HTMLElement>("[data-analytics-section]")?.dataset.analyticsSection;
}

// Mounted once in the root layout. Initializes Amplitude, sends a page view
// per route, and listens for three kinds of clicks: marked CTAs (the same
// data-analytics-* attributes prefect.io uses), links into the docs, and
// links that leave gofastmcp.com. Prefect-bound links also get the device id.
export function Analytics() {
  const pathname = usePathname();

  useEffect(() => {
    if (!analyticsEnabled()) return;
    init();
  }, []);

  useEffect(() => {
    if (!analyticsEnabled()) return;
    trackPageView(pathname);
  }, [pathname]);

  useEffect(() => {
    if (!analyticsEnabled()) return;

    const onClick = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (!(link instanceof HTMLAnchorElement)) return;
      const section = sectionOf(link);

      const cta = event.target.closest(CTA_SELECTOR);
      if (cta instanceof HTMLElement && cta.dataset.analyticsCtaName) {
        trackCtaClicked({
          cta_name: cta.dataset.analyticsCtaName,
          cta_text: cta.dataset.analyticsCtaText ?? (cta.textContent ?? "").replace(/\s+/g, " ").trim(),
          cta_destination: cta.dataset.analyticsCtaDestination ?? link.href,
          page_path: window.location.pathname,
          page_section: cta.dataset.analyticsPageSection ?? section ?? "unknown",
          cta_variant: cta.dataset.analyticsCtaVariant,
        });
      }

      let destination: URL;
      try {
        destination = new URL(link.href, window.location.origin);
      } catch {
        return;
      }
      if (destination.hostname !== window.location.hostname) {
        trackOutboundClick(link, destination, section);
      } else if (destination.pathname === "/docs" || destination.pathname.startsWith("/docs/")) {
        trackDocsLinkClicked(link, destination, section);
      }
    };

    // Decorate on the gesture that precedes navigation so the id is fresh.
    const onIntent = (event: Event) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (link instanceof HTMLAnchorElement) decorateWithDeviceId(link);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Enter") onIntent(event);
    };

    document.addEventListener("click", onClick, true);
    document.addEventListener("mousedown", onIntent, true);
    document.addEventListener("touchstart", onIntent, { capture: true, passive: true });
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("mousedown", onIntent, true);
      document.removeEventListener("touchstart", onIntent, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, []);

  return null;
}
