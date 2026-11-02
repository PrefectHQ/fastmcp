import type { SimpleIcon } from "simple-icons";
import { BrandIcon, ICONS } from "@/components/brand-icons";
import { docs } from "@/lib/links";

// The AI clients and SDKs the docs integrate with, each linking to its guide.
const CLIENTS: Array<{ name: string; icon: SimpleIcon; href: string }> = [
  { name: "Claude Code", icon: ICONS.claude, href: docs("integrations/claude-code") },
  { name: "Claude Desktop", icon: ICONS.claude, href: docs("integrations/claude-desktop") },
  { name: "ChatGPT", icon: ICONS.openai, href: docs("integrations/chatgpt") },
  { name: "Cursor", icon: ICONS.cursor, href: docs("integrations/cursor") },
  { name: "Gemini CLI", icon: ICONS.gemini, href: docs("integrations/gemini-cli") },
  { name: "Anthropic SDK", icon: ICONS.anthropic, href: docs("integrations/anthropic") },
  { name: "OpenAI SDK", icon: ICONS.openai, href: docs("integrations/openai") },
  { name: "Pydantic AI", icon: ICONS.pydantic, href: docs("integrations/pydantic-ai") },
];

export function Clients() {
  return (
    <section data-analytics-section="clients" className="border-y border-white/10 bg-white/[0.02] py-10">
      <div className="mx-auto w-full max-w-7xl px-6 lg:px-8">
        <p className="text-center text-body-s text-white/50">
          Works with the clients and SDKs you already use
        </p>
        <ul className="mt-6 flex flex-wrap items-center justify-center gap-x-9 gap-y-5">
          {CLIENTS.map((client) => (
            <li key={client.name}>
              <a
                href={client.href}
                className="inline-flex items-center gap-2.5 text-[15px] font-medium text-white/65 transition-colors hover:text-white"
              >
                <BrandIcon icon={client.icon} className="h-5 w-5" />
                {client.name}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
