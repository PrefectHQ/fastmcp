import { ArrowUpRight, Bot } from "lucide-react";
import { ButtonLink } from "@/components/button";
import { CodeBlock } from "@/components/code-block";
import { Section, SectionHeading } from "@/components/section";
import { AGENTS_SETUP, SITE_URL, docs } from "@/lib/links";

const PROMPT = `Help me set up FastMCP in this project. Read ${SITE_URL}/llms.txt and the current installation instructions. Check the Python environment, package manager and existing dependencies first, and ask before installing anything. Connect the documentation MCP server at ${SITE_URL}/mcp using my agent's settings, then verify the server locally without publishing it.`;

const STEPS = [
  {
    title: "Give it the docs index",
    body: "llms.txt lists every page of the documentation as Markdown. An agent that reads it first builds against the current API instead of what it remembers.",
    code: `${SITE_URL}/llms.txt`,
  },
  {
    title: "Add the documentation MCP server",
    body: "The docs are also an MCP server. Add the URL to your agent's MCP settings and it can search the documentation while it works.",
    code: `${SITE_URL}/mcp`,
  },
  {
    title: "Install the fastmcp-server skill",
    body: "An optional skill with the maintainers' workflow for building, extending and reviewing servers: pick the right MCP interfaces, keep schemas and errors useful, and verify through a real client.",
    code: "npx skills add https://gofastmcp.com --skill fastmcp-server",
  },
];

export function Agents() {
  return (
    <Section bordered name="agents" className="relative overflow-hidden">
      <div className="grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-white/16 px-3 py-1 font-mono text-[12px] tracking-[1.5px] text-peach uppercase">
            <Bot className="h-3.5 w-3.5" aria-hidden />
            For coding agents
          </div>
          <SectionHeading
            className="mt-6"
            title="Build it with your coding agent"
            lede="FastMCP's documentation is written for agents as much as for people. Point Claude Code, Cursor or any MCP-capable agent at it and the agent can scaffold, test and debug a server with version-matched guidance."
          />
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <ButtonLink
              href={AGENTS_SETUP}
              external
              analytics={{ name: "agents_setup_guide", section: "agents" }}
            >
              Agent setup guide
            </ButtonLink>
            <ButtonLink
              href={docs("getting-started/ai-assisted-development")}
              variant="secondary"
              analytics={{ name: "agents_ai_assisted_docs", section: "agents" }}
            >
              AI-assisted development docs
            </ButtonLink>
          </div>
          <p className="mt-4 text-body-s text-white/50">
            The setup guide on prefect.io has one-click installs for Claude Code, Cursor, Codex and
            other agents.
          </p>
        </div>

        <div className="space-y-4">
          <ol className="space-y-4">
            {STEPS.map((step, index) => (
              <li
                key={step.title}
                className="rounded-2xl border border-white/10 bg-card p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
              >
                <div className="flex items-start gap-4">
                  <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/16 font-mono text-[12px] text-white/70">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-[18px] leading-6 font-semibold tracking-normal text-foreground">
                      {step.title}
                    </h3>
                    <p className="mt-1.5 text-body-s leading-relaxed text-muted-foreground">{step.body}</p>
                    <code className="mt-3 block overflow-x-auto rounded-md border border-white/10 bg-code-bg px-3 py-2 font-mono text-[12.5px] text-code-text">
                      {step.code}
                    </code>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <div>
            <p className="mb-2 flex items-center gap-1.5 text-body-s text-white/50">
              A starter prompt that works in any agent
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </p>
            <CodeBlock code={PROMPT} language="text" className="[&_pre]:text-[12.5px] [&_pre]:whitespace-pre-wrap" />
          </div>
        </div>
      </div>
    </Section>
  );
}
