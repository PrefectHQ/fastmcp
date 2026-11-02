// The docs are a Mintlify site built from ../docs. Mintlify serves them under
// the /docs base path ("Host at" in the Mintlify dashboard) and this app
// proxies that origin, so gofastmcp.com/docs/* is the docs and / is this site.
// next.config.ts proxies the pages; proxy.ts proxies the asset and API paths
// that need an Origin header.
export const DOCS_ORIGIN = "https://fastmcp.mintlify.site";

// Mintlify's generated sitemap lists its own origin, and search engines skip
// sitemap URLs on a different host, so each entry is moved onto gofastmcp.com.
export function rewriteDocsSitemap(xml: string, docsSiteUrl: string): string {
  return xml.replace(/<loc>https?:\/\/[^/<]+\/docs(?=[/<])/g, `<loc>${docsSiteUrl}`);
}
