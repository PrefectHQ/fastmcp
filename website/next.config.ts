import type { NextConfig } from "next";
import { DOCS_ORIGIN } from "./lib/docs-proxy";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  async rewrites() {
    return {
      beforeFiles: [
        // Docs, proxied from Mintlify. Exact /docs first: an empty `:path*`
        // would target /docs/. Mintlify loads assets from the domain root;
        // /_mintlify and /api/request are proxied in proxy.ts.
        { source: "/docs", destination: `${DOCS_ORIGIN}/docs` },
        { source: "/docs/sitemap.xml", destination: "/api/docs-sitemap" },
        { source: "/docs/:path*", destination: `${DOCS_ORIGIN}/docs/:path*` },
        { source: "/mintlify-assets/:path+", destination: `${DOCS_ORIGIN}/mintlify-assets/:path+` },
        // Agent-facing files keep their root URLs (the README, the Prefect
        // agents page and existing MCP client configs point at them), but
        // Mintlify serves them under the base path. MCP clients do not
        // reliably follow redirects, so these are rewrites.
        { source: "/llms.txt", destination: `${DOCS_ORIGIN}/docs/llms.txt` },
        { source: "/llms-full.txt", destination: `${DOCS_ORIGIN}/docs/llms-full.txt` },
        { source: "/mcp", destination: `${DOCS_ORIGIN}/docs/mcp` },
        { source: "/skill.md", destination: `${DOCS_ORIGIN}/docs/skill.md` },
        { source: "/.well-known/mcp", destination: `${DOCS_ORIGIN}/docs/.well-known/mcp` },
        { source: "/.well-known/mcp.json", destination: `${DOCS_ORIGIN}/docs/.well-known/mcp.json` },
        { source: "/.well-known/mcp/:path+", destination: `${DOCS_ORIGIN}/docs/.well-known/mcp/:path+` },
        { source: "/.well-known/skills/:path+", destination: `${DOCS_ORIGIN}/docs/.well-known/skills/:path+` },
        {
          source: "/.well-known/agent-skills/:path+",
          destination: `${DOCS_ORIGIN}/docs/.well-known/agent-skills/:path+`,
        },
        { source: "/.well-known/vercel/:path+", destination: `${DOCS_ORIGIN}/.well-known/vercel/:path+` },
      ],
    };
  },
};

export default nextConfig;
