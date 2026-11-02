import Image from "next/image";
import { ArrowRight } from "lucide-react";
import { BrandIcon, ICONS } from "@/components/brand-icons";
import { CodeBlock } from "@/components/code-block";
import { Section, SectionHeading } from "@/components/section";
import { GITHUB_TS, docs } from "@/lib/links";

const PILLARS = [
  {
    name: "Servers",
    image: "/brand/servers-card.png",
    body: "Turn application logic into MCP capabilities with generated schemas and validation.",
    href: docs("servers/server"),
    code: `@mcp.tool
def refund_status(order_id: str) -> dict:
    """Look up a refund by order id."""
    return orders.refund(order_id)`,
  },
  {
    name: "Clients",
    image: "/brand/clients-card.png",
    body: "Connect to local or remote servers with full protocol support, from Python or the CLI.",
    href: docs("clients/client"),
    code: `async with Client(url) as client:
    result = await client.call_tool(
        "refund_status",
        {"order_id": "A-1042"},
    )
    print(result.data)`,
  },
  {
    name: "Apps",
    image: "/brand/apps-card.png",
    body: "Let a tool return forms, tables and charts that render inside the conversation.",
    href: docs("apps/overview"),
    code: `@mcp.tool(app=True)
def refunds() -> PrefabApp:
    with PrefabApp() as app:
        DataTable(rows=orders.refunds())
    return app`,
  },
];

export function Pillars() {
  return (
    <Section bordered name="pillars">
      <SectionHeading
        title="Servers, clients and apps share one model"
        lede="FastMCP owns the protocol machinery. Your code defines what the application does. The three pillars use the same decorators, the same types and the same auth, so what you learn on one carries to the others."
      />
      <div className="mt-14 grid gap-6 lg:grid-cols-3">
        {PILLARS.map((pillar) => (
          <article
            key={pillar.name}
            className="flex flex-col overflow-hidden rounded-2xl border border-white/10 bg-card shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
          >
            <Image
              src={pillar.image}
              alt=""
              width={1194}
              height={895}
              className="aspect-[4/3] w-full object-cover"
            />
            <div className="flex flex-1 flex-col p-7">
              <h3 className="text-[22px] leading-7 font-semibold tracking-normal text-foreground">
                {pillar.name}
              </h3>
              <p className="mt-2 text-body-r leading-relaxed text-muted-foreground">{pillar.body}</p>
              <div className="mt-5">
                <CodeBlock code={pillar.code} language="python" className="rounded-lg [&_pre]:p-4 [&_pre]:text-[12px]" />
              </div>
              <a
                href={pillar.href}
                className="group mt-6 inline-flex items-center gap-1.5 text-body-s font-medium text-white/80 hover:text-white"
              >
                {pillar.name} docs
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
              </a>
            </div>
          </article>
        ))}
      </div>
      <p className="mt-10 flex flex-wrap items-center gap-x-2 gap-y-1 text-body-s text-muted-foreground">
        <BrandIcon icon={ICONS.typescript} className="h-4 w-4 text-white/60" />
        Building in TypeScript?
        <a href={GITHUB_TS} target="_blank" rel="noopener noreferrer" className="font-medium text-white underline-offset-4 hover:underline">
          FastMCP for TypeScript
        </a>
        is the official counterpart from the same team, with the same servers, clients and apps.
      </p>
    </Section>
  );
}
