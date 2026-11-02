import { ArrowRight, ArrowUpRight } from "lucide-react";
import { Section, SectionHeading } from "@/components/section";
import { docs, prefect } from "@/lib/links";

type Guide = { title: string; body: string; href: string; external?: boolean };

const LEARN: Guide[] = [
  {
    title: "Install FastMCP",
    body: "Add it with uv or pip, verify the package and find the right upgrade guide.",
    href: docs("getting-started/installation"),
  },
  {
    title: "Quickstart: your first server",
    body: "Create a tool, run its server, call it from a client and add a UI, in a few minutes.",
    href: docs("getting-started/quickstart"),
  },
  {
    title: "Give a tool an interface",
    body: "Return a chart and a searchable table from a single Python tool.",
    href: docs("apps/quickstart"),
  },
  {
    title: "Connect a client",
    body: "Call any MCP server from Python, in memory, over stdio or over HTTP.",
    href: docs("clients/client"),
  },
  {
    title: "Add authentication",
    body: "Pick between token verification, an OAuth proxy and a full OAuth server.",
    href: docs("servers/auth/authentication"),
  },
  {
    title: "Generate a server from OpenAPI or FastAPI",
    body: "Turn an existing API into MCP tools without rewriting it.",
    href: docs("integrations/openapi"),
  },
  {
    title: "Use it from Claude Code, Cursor or ChatGPT",
    body: "Install a server into the client you work in, including the config it expects.",
    href: docs("integrations/claude-code"),
  },
  {
    title: "Upgrading to FastMCP 4",
    body: "What changed from FastMCP 3 and from the MCP SDKs, with a change register.",
    href: docs("getting-started/upgrading/from-fastmcp-3"),
  },
];

const DEPLOY: Guide[] = [
  {
    title: "Run your server",
    body: "Local development, stdio and streamable HTTP, auto-reload and the CLI.",
    href: docs("deployment/running-server"),
  },
  {
    title: "Serve over HTTP",
    body: "Expose a server on a URL, behind your own ASGI app if you want.",
    href: docs("deployment/http"),
  },
  {
    title: "Configure with fastmcp.json",
    body: "Declare entrypoint, dependencies and environment once, for the CLI and for hosting.",
    href: docs("deployment/server-configuration"),
  },
  {
    title: "Deploy to FastMCP Gateway",
    body: "Push from GitHub and get a live, OAuth-protected URL. Free for personal projects.",
    href: docs("deployment/fastmcp-gateway"),
  },
  {
    title: "Host, govern and audit for a team",
    body: "A private registry, tool-level access control and an audit trail, from the FastMCP team at Prefect.",
    href: prefect("/fastmcp", "get-started"),
    external: true,
  },
];

function GuideList({ guides }: { guides: Guide[] }) {
  return (
    <ul className="divide-y divide-white/10 border-y border-white/10">
      {guides.map((guide) => {
        const Icon = guide.external ? ArrowUpRight : ArrowRight;
        return (
          <li key={guide.title}>
            <a
              href={guide.href}
              {...(guide.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              className="group flex items-start justify-between gap-6 py-4 transition-colors hover:bg-white/[0.03]"
            >
              <div>
                <span className="block text-[16px] leading-6 font-semibold text-foreground">{guide.title}</span>
                <span className="mt-1 block text-body-s leading-relaxed text-muted-foreground">{guide.body}</span>
              </div>
              <Icon className="mt-1 h-4 w-4 shrink-0 text-white/40 transition-transform group-hover:translate-x-0.5 group-hover:text-white" aria-hidden />
            </a>
          </li>
        );
      })}
    </ul>
  );
}

export function GetStarted() {
  return (
    <Section bordered id="get-started" name="get-started">
      <SectionHeading
        title="Get started"
        lede="Pick the guide that matches where you are. Each one is a short page in the docs, with code you can paste."
      />
      <div className="mt-12 grid gap-12 lg:grid-cols-[1.2fr_0.8fr] lg:gap-16">
        <div>
          <h3 className="mb-4 font-mono text-tag text-white/50 uppercase">Learn</h3>
          <GuideList guides={LEARN} />
        </div>
        <div>
          <h3 className="mb-4 font-mono text-tag text-white/50 uppercase">Deploy</h3>
          <GuideList guides={DEPLOY} />
        </div>
      </div>
    </Section>
  );
}
