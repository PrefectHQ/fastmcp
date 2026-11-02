import { ArrowUpRight } from "lucide-react";
import type { SimpleIcon } from "simple-icons";
import { BrandIcon, ICONS } from "@/components/brand-icons";
import { Section, SectionHeading } from "@/components/section";
import { BLOG_MCP, BLUESKY, DISCORD, DISCUSSIONS, GITHUB, RELEASES, X_PROFILE, docs, prefect } from "@/lib/links";
import { formatCount, type ProjectStats } from "@/lib/stats";

// Hand-picked from prefect.io/blog?category=mcp. Update when a better post
// lands; the "More on the blog" link always shows the current list.
const POSTS = [
  {
    title: "How to tell if your MCP server is actually good",
    date: "Sep 2026",
    href: prefect("/blog/is-your-mcp-server-actually-good", "community-blog"),
  },
  {
    title: "FastMCP 4 is GA",
    date: "Aug 2026",
    href: prefect("/blog/fastmcp-4-is-ga", "community-blog"),
  },
  {
    title: "What the “C” in MCP actually means",
    date: "Jul 2026",
    href: prefect("/blog/what-the-c-in-mcp-means", "community-blog"),
  },
];

const PLACES: Array<{ label: string; body: string; href: string; icon: SimpleIcon }> = [
  { label: "Discord", body: "Ask questions and show what you built.", href: DISCORD, icon: ICONS.discord },
  { label: "GitHub Discussions", body: "Design threads and feature ideas.", href: DISCUSSIONS, icon: ICONS.github },
  { label: "X", body: "Release notes and short demos.", href: X_PROFILE, icon: ICONS.x },
  { label: "Bluesky", body: "The same, on the other network.", href: BLUESKY, icon: ICONS.bluesky },
];

export function Community({ stats }: { stats: ProjectStats }) {
  const counters = [
    { value: formatCount(stats.stars), label: "stars", href: GITHUB },
    { value: formatCount(stats.forks), label: "forks", href: `${GITHUB}/forks` },
    { value: formatCount(stats.contributors), label: "contributors", href: `${GITHUB}/graphs/contributors` },
    { value: stats.latestRelease, label: "latest release", href: RELEASES },
  ];

  return (
    <Section bordered name="community">
      <div className="grid gap-12 lg:grid-cols-[1fr_1fr] lg:gap-16">
        <div>
          <SectionHeading
            title="Built in the open, with a lot of help"
            lede="FastMCP created the high-level API that went into the official MCP Python SDK in 2024, and the standalone project has grown with its contributors ever since. Everything is Apache 2.0 and developed on GitHub."
          />
          <dl className="mt-10 grid grid-cols-2 gap-6 sm:grid-cols-4">
            {counters.map((counter) => (
              <a key={counter.label} href={counter.href} target="_blank" rel="noopener noreferrer" className="group">
                <dd className="text-[32px] leading-none font-semibold tracking-[-1px] text-foreground tabular-nums group-hover:text-white">
                  {counter.value}
                </dd>
                <dt className="mt-2 text-body-s text-muted-foreground">{counter.label}</dt>
              </a>
            ))}
          </dl>
          <ul className="mt-10 grid gap-3 sm:grid-cols-2">
            {PLACES.map((place) => (
              <li key={place.label}>
                <a
                  href={place.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-start gap-3 rounded-xl border border-white/10 p-4 transition-colors hover:border-white/25"
                >
                  <BrandIcon icon={place.icon} className="mt-0.5 h-4.5 w-4.5 text-white/70" />
                  <span>
                    <span className="block text-[15px] font-semibold text-foreground">{place.label}</span>
                    <span className="block text-body-s text-muted-foreground">{place.body}</span>
                  </span>
                </a>
              </li>
            ))}
          </ul>
          <p className="mt-6 text-body-s text-muted-foreground">
            Want to contribute? Start with the{" "}
            <a href={docs("development/contributing")} className="font-medium text-white underline-offset-4 hover:underline">
              contributing guide
            </a>
            . Issues tagged <span className="font-mono text-[13px] text-white/80">prs welcome</span> are open to anyone.
          </p>
        </div>

        <div>
          <a
            href={docs("getting-started/whats-new")}
            className="group flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-card p-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] transition-colors hover:border-white/25"
          >
            <span>
              <span className="font-mono text-[12px] tracking-[1.5px] text-peach uppercase">What&apos;s new</span>
              <span className="mt-2 block text-[20px] leading-7 font-semibold text-foreground">
                FastMCP 4 is built for the new MCP protocol
              </span>
              <span className="mt-1 block text-body-s text-muted-foreground">
                Protocol negotiation, interactive tools, session state, background work, server extensions and enterprise identity.
              </span>
            </span>
            <ArrowUpRight className="h-5 w-5 shrink-0 text-white/50 transition-transform group-hover:translate-x-0.5 group-hover:text-white" aria-hidden />
          </a>

          <h3 className="mt-10 mb-4 font-mono text-tag text-white/50 uppercase">From the blog</h3>
          <ul className="divide-y divide-white/10 border-y border-white/10">
            {POSTS.map((post) => (
              <li key={post.href}>
                <a
                  href={post.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-center justify-between gap-6 py-4 transition-colors hover:bg-white/[0.03]"
                >
                  <span>
                    <span className="block text-[16px] leading-6 font-semibold text-foreground">{post.title}</span>
                    <span className="mt-1 block text-body-s text-muted-foreground">{post.date}</span>
                  </span>
                  <ArrowUpRight className="h-4 w-4 shrink-0 text-white/40 group-hover:text-white" aria-hidden />
                </a>
              </li>
            ))}
          </ul>
          <a
            href={BLOG_MCP}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-5 inline-flex items-center gap-1.5 text-body-s font-medium text-white/80 hover:text-white"
          >
            More on the blog, including the FastMCP podcast
            <ArrowUpRight className="h-4 w-4" aria-hidden />
          </a>
        </div>
      </div>
    </Section>
  );
}
