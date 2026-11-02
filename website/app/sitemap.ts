import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/links";

// The homepage only. The docs publish their own sitemap at /docs/sitemap.xml
// (app/api/docs-sitemap), and robots.ts lists both.
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: `${SITE_URL}/`, changeFrequency: "weekly", priority: 1 }];
}
