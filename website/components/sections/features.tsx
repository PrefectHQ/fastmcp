import {
  ArrowRight,
  Blocks,
  Cable,
  LayoutPanelTop,
  ShieldCheck,
  Terminal,
  Wand2,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { CodeBlock } from "@/components/code-block";
import { Section, SectionHeading } from "@/components/section";
import { docs } from "@/lib/links";
import { cn } from "@/lib/utils";

type Feature = {
  title: string;
  body: string;
  href: string;
  icon: LucideIcon;
  /** Wide cards span two columns and show a code excerpt beside the copy. */
  code?: { language: string; source: string };
};

// Eight capabilities, each one a docs link. Four are wide and carry a short,
// documented code excerpt so the grid reads as the product rather than a
// list of nouns; wide and narrow alternate so every three-column row fills.
const FEATURES: Feature[] = [
  {
    title: "Tools, resources and prompts",
    body: "Decorate a function and FastMCP derives the schema from its signature and docstring, validates inputs and outputs, and serves it over MCP. Resources and prompts work the same way.",
    href: docs("servers/tools"),
    icon: Wrench,
    code: {
      language: "python",
      source: `@mcp.tool
def search(query: str, limit: int = 10) -> list[Doc]:
    """Search the knowledge base."""
    return index.search(query, limit=limit)`,
    },
  },
  {
    title: "Interactive apps",
    body: "A tool can return a form, a table or a chart that renders in the conversation, with prefab components for approvals, choices and file uploads.",
    href: docs("apps/overview"),
    icon: LayoutPanelTop,
  },
  {
    title: "Clients",
    body: "Connect to any server, local or remote, from Python or the CLI. Transport negotiation, OAuth and the protocol lifecycle are handled for you.",
    href: docs("clients/client"),
    icon: Cable,
  },
  {
    title: "Authentication",
    body: "Token verification, an OAuth or OIDC proxy, or a full OAuth server, with 17 provider guides from Auth0 to WorkOS. One argument on the server turns it on.",
    href: docs("servers/auth/authentication"),
    icon: ShieldCheck,
    code: {
      language: "python",
      source: `auth = GitHubProvider(
    client_id=os.environ["GITHUB_CLIENT_ID"],
    client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    base_url="https://tools.example.com",
)
mcp = FastMCP("Secured app", auth=auth)`,
    },
  },
  {
    title: "Tool transformation",
    body: "Rename, reshape, hide and search tools so each agent sees the right surface. Code mode lets a model work through large tool sets without flooding its context.",
    href: docs("servers/transforms/transforms"),
    icon: Wand2,
    code: {
      language: "python",
      source: `find_items = Tool.from_tool(
    search,
    name="find_items",
    transform_args={"q": ArgTransform(name="query")},
)`,
    },
  },
  {
    title: "Composition and proxies",
    body: "Mount servers inside each other, proxy remote ones, and generate servers from OpenAPI specs and FastAPI apps.",
    href: docs("servers/providers/overview"),
    icon: Blocks,
  },
  {
    title: "Middleware and background tasks",
    body: "Dependency injection, lifespans, middleware for logging and rate limits, and tasks that keep running after the request returns.",
    href: docs("servers/middleware"),
    icon: Workflow,
  },
  {
    title: "CLI and testing",
    body: "Run, inspect and install servers from the command line, test them in memory with no network, and emit telemetry.",
    href: docs("cli/overview"),
    icon: Terminal,
    code: {
      language: "bash",
      source: `$ fastmcp dev server.py
$ fastmcp inspect server.py
$ fastmcp install claude-code server.py`,
    },
  },
];

function IconTile({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/12 bg-white/[0.04] text-white/80 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] transition-colors group-hover:border-white/30 group-hover:text-white">
      <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden />
    </span>
  );
}

function FeatureCard({ feature }: { feature: Feature }) {
  const wide = Boolean(feature.code);
  return (
    <a
      href={feature.href}
      className={cn(
        "group relative flex h-full flex-col overflow-hidden rounded-2xl border border-white/10 bg-card shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-colors hover:border-white/25",
        wide && "md:grid md:grid-cols-[1fr_1.1fr]",
      )}
    >
      {/* A faint light from the top corner on hover: elevation by inset
          light, never a drop shadow or a filled color. */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_0%_0%,rgba(255,255,255,0.06),transparent_70%)] opacity-0 transition-opacity duration-300 group-hover:opacity-100"
      />
      <div className="relative flex flex-1 flex-col p-7">
        <IconTile icon={feature.icon} />
        <h3 className="mt-5 text-[20px] leading-7 font-semibold tracking-normal text-foreground">
          {feature.title}
        </h3>
        <p className="mt-2.5 flex-1 text-body-r leading-relaxed text-muted-foreground [text-wrap:pretty]">
          {feature.body}
        </p>
        <span className="mt-6 inline-flex items-center gap-1.5 text-body-s font-medium text-white/80 group-hover:text-white">
          Read the docs
          <ArrowRight
            className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </span>
      </div>
      {feature.code && (
        <div className="relative flex items-center border-t border-white/10 bg-code-bg/60 p-4 md:border-t-0 md:border-l md:p-5 md:pl-6">
          <CodeBlock
            code={feature.code.source}
            language={feature.code.language}
            className="w-full rounded-lg border-white/8 bg-transparent shadow-none [&_pre]:p-3 [&_pre]:text-[12.5px] md:[&_pre]:p-4"
          />
        </div>
      )}
    </a>
  );
}

export function Features() {
  return (
    <Section name="features">
      <SectionHeading
        title="Everything you need to build on MCP"
        lede="An effective MCP application needs more than a function registry. Models need accurate schemas, callers need validated results, clients need compatible transports and production servers need auth. FastMCP treats all of it as framework work."
      />
      <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((feature) => (
          <li key={feature.title} className={cn(feature.code && "sm:col-span-2")}>
            <FeatureCard feature={feature} />
          </li>
        ))}
      </ul>
    </Section>
  );
}
