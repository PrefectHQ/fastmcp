import { DOCS_ORIGIN, rewriteDocsSitemap } from "@/lib/docs-proxy";
import { SITE_URL } from "@/lib/links";

// Served at /docs/sitemap.xml through a rewrite in next.config.ts. Mintlify's
// sitemap names its own origin; each entry is moved onto gofastmcp.com/docs.
export async function GET() {
  let xml: string | null = null;
  try {
    const response = await fetch(`${DOCS_ORIGIN}/docs/sitemap.xml`, {
      next: { revalidate: 3600 },
    });
    xml = response.ok ? await response.text() : null;
  } catch {
    xml = null;
  }

  if (xml === null) {
    return new Response("The docs sitemap could not be fetched.", { status: 502 });
  }

  return new Response(rewriteDocsSitemap(xml, `${SITE_URL}/docs`), {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
