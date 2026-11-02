// Every destination the homepage links to, in one place. Docs links are
// same-origin paths under /docs (see lib/docs-proxy.ts); Prefect links carry
// UTM parameters the same way the docs tag theirs.

export const SITE_URL = "https://gofastmcp.com";
export const DOCS = "/docs";

export const docs = (path: string) => `${DOCS}/${path.replace(/^\//, "")}`;

export const GITHUB = "https://github.com/PrefectHQ/fastmcp";
export const GITHUB_TS = "https://github.com/PrefectHQ/fastmcp-ts";
export const RELEASES = `${GITHUB}/releases`;
export const DISCUSSIONS = `${GITHUB}/discussions`;
export const ISSUES = `${GITHUB}/issues`;
export const DISCORD = "https://discord.gg/uu8dJCgttd";
export const X_PROFILE = "https://x.com/fastmcp";
export const BLUESKY = "https://bsky.app/profile/gofastmcp.com";
export const PYPI = "https://pypi.org/project/fastmcp/";
export const NPM_TS = "https://www.npmjs.com/package/@prefecthq/fastmcp-ts";
export const MCP_SPEC = "https://modelcontextprotocol.io/";

const PREFECT_ORIGIN = "https://www.prefect.io";
const GATEWAY_APP_ORIGIN = "https://horizon.prefect.io";

function withUtm(url: string, content: string) {
  const u = new URL(url);
  u.searchParams.set("utm_source", "gofastmcp");
  u.searchParams.set("utm_medium", "website");
  u.searchParams.set("utm_campaign", "homepage");
  u.searchParams.set("utm_content", content);
  return u.toString();
}

export const prefect = (path: string, content: string) =>
  withUtm(`${PREFECT_ORIGIN}${path}`, content);

export const gatewaySignup = (content: string) =>
  withUtm(`${GATEWAY_APP_ORIGIN}/signup`, content);

export const BLOG_MCP = prefect("/blog?category=mcp", "blog");
export const AGENTS_SETUP = prefect("/agents?product=fastmcp&agent=claude-code", "agents");
