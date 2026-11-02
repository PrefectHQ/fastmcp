import Image from "next/image";
import {
  AGENTS_SETUP,
  BLOG_MCP,
  BLUESKY,
  DISCORD,
  DISCUSSIONS,
  GITHUB,
  GITHUB_TS,
  RELEASES,
  X_PROFILE,
  docs,
  gatewaySignup,
  prefect,
} from "@/lib/links";
import { BrandIcon, ICONS } from "./brand-icons";

type FooterLink = { label: string; href: string; external?: boolean };

// Same shape as the Next.js footer: a handful of short columns, then a
// bottom bar with the maintainer's mark and the social icons.
const COLUMNS: Array<{ title: string; links: FooterLink[] }> = [
  {
    title: "Resources",
    links: [
      { label: "Docs", href: docs("getting-started/welcome") },
      { label: "Quickstart", href: docs("getting-started/quickstart") },
      { label: "Integrations", href: docs("integrations/claude-code") },
      { label: "CLI reference", href: docs("cli/overview") },
      { label: "Upgrade guides", href: docs("getting-started/upgrading/from-fastmcp-3") },
      { label: "Agent setup", href: AGENTS_SETUP, external: true },
      { label: "llms.txt", href: "/llms.txt" },
    ],
  },
  {
    title: "Community",
    links: [
      { label: "GitHub", href: GITHUB, external: true },
      { label: "Releases", href: RELEASES, external: true },
      { label: "Changelog", href: docs("changelog") },
      { label: "Discord", href: DISCORD, external: true },
      { label: "Discussions", href: DISCUSSIONS, external: true },
      { label: "Contributing", href: docs("development/contributing") },
      { label: "Blog", href: BLOG_MCP, external: true },
    ],
  },
  {
    title: "Deploy",
    links: [
      { label: "FastMCP Gateway", href: prefect("/fastmcp", "footer"), external: true },
      { label: "Start free", href: gatewaySignup("footer"), external: true },
      { label: "Deployment guide", href: docs("deployment/fastmcp-gateway") },
      { label: "MCP hosting", href: prefect("/fastmcp/hosting", "footer"), external: true },
      { label: "Private registry", href: prefect("/fastmcp/registry", "footer"), external: true },
      { label: "Remix servers", href: prefect("/fastmcp/remix", "footer"), external: true },
      { label: "Pricing", href: prefect("/pricing", "footer"), external: true },
    ],
  },
  {
    title: "Prefect",
    links: [
      { label: "About Prefect", href: prefect("/company", "footer"), external: true },
      { label: "Prefect open source", href: prefect("/prefect/open-source", "footer"), external: true },
      { label: "Prefect blog", href: prefect("/blog", "footer"), external: true },
      { label: "Careers", href: prefect("/careers", "footer"), external: true },
      { label: "Security", href: prefect("/security", "footer"), external: true },
      { label: "Talk to an engineer", href: prefect("/contact-sales/fastmcp", "footer"), external: true },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Privacy policy", href: prefect("/legal/privacy-policy", "footer"), external: true },
      { label: "Terms of use", href: prefect("/legal/website-tos", "footer"), external: true },
      { label: "License (Apache 2.0)", href: `${GITHUB}/blob/main/LICENSE`, external: true },
    ],
  },
];

const SOCIAL = [
  { label: "GitHub", href: GITHUB, icon: ICONS.github },
  { label: "Discord", href: DISCORD, icon: ICONS.discord },
  { label: "X", href: X_PROFILE, icon: ICONS.x },
  { label: "Bluesky", href: BLUESKY, icon: ICONS.bluesky },
];

export function Footer() {
  return (
    <footer className="border-t border-white/10 bg-background">
      <div className="mx-auto w-full max-w-7xl px-6 py-16 lg:px-8">
        <nav aria-label="Footer">
          <div className="grid grid-cols-2 gap-x-8 gap-y-12 md:grid-cols-3 lg:grid-cols-5">
            {COLUMNS.map((column) => (
              <div key={column.title}>
                <h2 className="font-mono text-tag text-white/50 uppercase">{column.title}</h2>
                <ul className="mt-5 space-y-3">
                  {column.links.map((link) => (
                    <li key={link.label}>
                      <a
                        href={link.href}
                        {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                        className="text-body-s text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </nav>

        <div className="mt-16 flex flex-col gap-8 border-t border-white/10 pt-8 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
            <a href="/" aria-label="FastMCP home" className="inline-flex items-center">
              <Image src="/brand/fastmcp-logo-reverse.svg" alt="" width={110} height={24} className="h-6 w-auto" />
            </a>
            <a
              href={prefect("/", "footer-lockup")}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-body-s text-muted-foreground transition-colors hover:text-foreground"
            >
              <span>Made with</span>
              <span aria-hidden>💙</span>
              <span>by</span>
              <Image src="/brand/prefect-logomark-white.svg" alt="" width={13} height={18} className="h-[18px] w-auto" />
              <span className="font-semibold text-foreground">Prefect</span>
            </a>
          </div>
          <div className="flex items-center gap-5">
            {SOCIAL.map((social) => (
              <a
                key={social.label}
                href={social.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={social.label}
                className="text-white/60 transition-colors hover:text-white"
              >
                <BrandIcon icon={social.icon} className="h-[18px] w-[18px]" />
              </a>
            ))}
          </div>
        </div>
        <div className="mt-6 flex flex-col gap-2 text-[12px] text-white/40 sm:flex-row sm:justify-between">
          <p>© {new Date().getFullYear()} Prefect Technologies, Inc. FastMCP is Apache 2.0 licensed.</p>
          <p>
            TypeScript?{" "}
            <a href={GITHUB_TS} target="_blank" rel="noopener noreferrer" className="underline-offset-4 hover:text-white hover:underline">
              FastMCP for TypeScript
            </a>
          </p>
        </div>
      </div>
    </footer>
  );
}
