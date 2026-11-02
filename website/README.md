# gofastmcp.com

The homepage for gofastmcp.com. A small Next.js app that owns the domain root
and proxies the Mintlify docs in `../docs` under `/docs`, the same arrangement
prefect.io uses for its own docs.

```sh
bun install
bun run dev            # http://localhost:3100
bun run check-types
bun run build
```

## How the domain is served

| Path                                  | Served by                                                    |
| ------------------------------------- | ------------------------------------------------------------ |
| `/`                                   | This app (`app/page.tsx`)                                    |
| `/docs`, `/docs/*`                    | Mintlify, proxied from `fastmcp.mintlify.site/docs/*`         |
| `/_mintlify/*`, `/api/request`        | Mintlify client assets and API, proxied in `proxy.ts`         |
| `/mcp`, `/llms.txt`, `/llms-full.txt` | Mintlify, rewritten to the `/docs` base path (URLs unchanged) |
| `/.well-known/mcp*`, `/.well-known/skills/*`, `/skill.md` | Same                                      |
| `/docs/sitemap.xml`                   | `app/api/docs-sitemap`, Mintlify's sitemap on this host       |
| `/sitemap.xml`, `/robots.txt`         | This app                                                     |
| Old root docs paths (`/servers/*` ...) | 308 redirects to `/docs/*` in `next.config.ts`               |

`lib/docs-proxy.ts` holds the Mintlify origin, `lib/links.ts` every URL the
page links to. Stars, downloads, contributor count and the latest release come
from public GitHub and PyPI endpoints at request time (`lib/stats.ts`), cached
for an hour, with known fallbacks.

## Cutover checklist

The code is ready before the domain moves. In order:

1. Create a Vercel project for this repository with **Root Directory**
   `website` (framework preset Next.js, Bun is detected from `bun.lock`).
2. In the Mintlify dashboard, under Custom domain, enable **Host at**, enter
   `gofastmcp.com` and the base path `docs`. From that moment the canonical
   docs URL is `fastmcp.mintlify.site/docs`, which is what this app proxies.
3. Point `gofastmcp.com` (and `www`) at the Vercel project.
4. Smoke test: `/`, `/docs`, `/docs/getting-started/quickstart`, an old path
   such as `/getting-started/quickstart` (expect a redirect), `/llms.txt`,
   `/mcp` (expect 405 on GET, the endpoint only speaks POST) and
   `/.well-known/mcp.json`.
5. Update the GitHub repository's website field and the README badges if they
   should point at `/docs` paths.

Until step 2 is done, docs links on a local build 404 because the Mintlify
origin has no `/docs` base path yet. The page itself renders regardless.

## Analytics

The same Amplitude setup as prefect.io and the docs' `fastmcp-analytics.js`:
the `@amplitude/analytics-browser` SDK reporting to the shared Prefect project,
sent through the same-origin `/api/t` proxy (remote config through
`/api/sr-cfg`) rather than Amplitude's hosts. `components/analytics.tsx`
sends one `Page View: FastMCP Website` per route, `cta_clicked` for buttons
carrying `data-analytics-*` attributes (the `analytics` prop on `ButtonLink`),
`Docs Link Clicked` for links into `/docs`, and `Website Outbound Clicked` for
links that leave the domain, each stamped with the enclosing
`data-analytics-section`. Links to Prefect domains carry the Amplitude device
id so the visitor stays one user across sites.

Tracking is off in `next dev` (set `NEXT_PUBLIC_ANALYTICS_IN_DEV=1` to test)
and for visitors sending Global Privacy Control or Do Not Track.

## Design

The site uses Prefect's design system so it reads as a sibling of prefect.io:
Articulat CF and DM Mono, the Night `#06000e` ground, white text on the
100/75/50 ladder, white hairlines at 16%, 4px button radius, 16px card radius,
96 to 128px between sections. Buttons are solid white or hairline, never
filled with a brand color; color lives in the FastMCP watercolor artwork and
nowhere else. Tokens are in `app/globals.css`, copied from the marketing
site's `styles/theme.css`.

Articulat CF is a licensed face and is not in this repository. The
`@font-face` rules load it from prefect.io, which serves the files with
permissive CORS.

## Page outline

- **Nav**: Docs, Blog (prefect.io/blog filtered to MCP), GitHub with the live
  star count, a "Get started" link to the quickstart and a "Deploy" button to
  the FastMCP Gateway signup.
- **Hero**: "The framework for MCP", install line, two CTAs, live stars and
  downloads, the README's six-line server beside the run command.
- **Clients strip**: the AI clients and SDKs with integration guides.
- **Everything you need to build on MCP**: eight docs-linked capability cards.
- **Built in the open**: live counters, community links, what's new in v4 and
  three posts from the blog.
- **Servers, clients and apps share one model**: the three pillars with a
  code excerpt each, plus the TypeScript counterpart.
- **Build it with your coding agent**: llms.txt, the docs MCP server, the
  `fastmcp-server` skill, a starter prompt, and the agent setup guide on
  prefect.io.
- **Get started**: a Learn lane (nine docs guides) and a Deploy lane that ends
  at FastMCP Gateway.
- **Closing CTA**: deploy on FastMCP Gateway, free for personal projects.
- **Footer**: Resources, Community, Deploy, Prefect, Legal, then a bottom bar
  with the Prefect lockup and social icons.
