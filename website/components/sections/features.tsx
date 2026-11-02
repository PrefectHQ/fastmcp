import { ArrowRight } from "lucide-react";
import { Section, SectionHeading } from "@/components/section";
import { docs } from "@/lib/links";
import { cn } from "@/lib/utils";

const FEATURES = [
  {
    title: "Tools, resources and prompts",
    body: "Decorate a function and FastMCP derives the schema from its signature and docstring, validates inputs and outputs, and serves it over MCP. Resources and prompts work the same way.",
    href: docs("servers/tools"),
    wide: true,
  },
  {
    title: "Interactive apps",
    body: "A tool can return a form, a table or a chart that renders in the conversation, with prefab components for approvals, choices and file uploads.",
    href: docs("apps/overview"),
  },
  {
    title: "Clients",
    body: "Connect to any server, local or remote, from Python or the CLI. Transport negotiation, OAuth and the protocol lifecycle are handled for you.",
    href: docs("clients/client"),
  },
  {
    title: "Authentication",
    body: "Token verification, an OAuth or OIDC proxy, or a full OAuth server, with 17 provider guides from Auth0 to WorkOS.",
    href: docs("servers/auth/authentication"),
    wide: true,
  },
  {
    title: "Tool transformation",
    body: "Rename, reshape, hide and search tools so each agent sees the right surface. Code mode lets a model work through large tool sets without flooding its context.",
    href: docs("servers/transforms/transforms"),
    wide: true,
  },
  {
    title: "Composition and proxies",
    body: "Mount servers inside each other, proxy remote ones, and generate servers from OpenAPI specs and FastAPI apps.",
    href: docs("servers/providers/overview"),
  },
  {
    title: "Middleware and background tasks",
    body: "Dependency injection, lifespans, middleware for logging and rate limits, and tasks that keep running after the request returns.",
    href: docs("servers/middleware"),
  },
  {
    title: "CLI and testing",
    body: "Run, inspect and install servers from the command line, test them in memory with no network, and emit telemetry.",
    href: docs("cli/overview"),
    wide: true,
  },
];

export function Features() {
  return (
    <Section name="features">
      <SectionHeading
        title="Everything you need to build on MCP"
        lede="An effective MCP application needs more than a function registry. Models need accurate schemas, callers need validated results, clients need compatible transports and production servers need auth. FastMCP treats all of it as framework work."
      />
      <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((feature) => (
          <li key={feature.title} className={cn(feature.wide && "lg:col-span-2")}>
            <a
              href={feature.href}
              className="group flex h-full flex-col rounded-2xl border border-white/10 bg-card p-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-colors hover:border-white/25"
            >
              <h3 className="text-[20px] leading-7 font-semibold tracking-normal text-foreground">
                {feature.title}
              </h3>
              <p className="mt-3 flex-1 text-body-r leading-relaxed text-muted-foreground [text-wrap:pretty]">
                {feature.body}
              </p>
              <span className="mt-6 inline-flex items-center gap-1.5 text-body-s font-medium text-white/80 group-hover:text-white">
                Docs
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
              </span>
            </a>
          </li>
        ))}
      </ul>
    </Section>
  );
}
