import Image from "next/image";
import { Download, Server, Star } from "lucide-react";
import type { ReactNode } from "react";
import { CodeBlock } from "@/components/code-block";
import { CopyCommand } from "@/components/copy-command";
import { ButtonLink } from "@/components/button";
import { GITHUB, PYPI, docs } from "@/lib/links";
import type { ProjectStats } from "@/lib/stats";
import { formatCount } from "@/lib/stats";

const SERVER = `from fastmcp import FastMCP

mcp = FastMCP("Demo 🚀")


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    mcp.run()`;

const RUN = `$ uv add fastmcp
$ fastmcp run server.py --transport http`;

function Stat({ icon, value, label, href }: { icon: ReactNode; value: string; label: string; href?: string }) {
  const body = (
    <>
      <span className="text-white/50">{icon}</span>
      <span className="font-semibold text-white tabular-nums">{value}</span>
      <span className="text-white/60">{label}</span>
    </>
  );
  const className = "inline-flex items-center gap-2 text-body-s";
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`${className} transition-colors hover:text-white`}>
      {body}
    </a>
  ) : (
    <span className={className}>{body}</span>
  );
}

export function Hero({ stats }: { stats: ProjectStats }) {
  return (
    <section data-analytics-section="hero" className="relative overflow-hidden pt-[calc(var(--nav-h)+72px)] pb-20 md:pt-[calc(var(--nav-h)+104px)] md:pb-28">
      {/* The watercolor "F" from the docs, the one place color is allowed to
          live on the page. It sits behind the code column and fades out
          toward the copy so the chrome stays monochrome. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <Image
          src="/brand/f-watercolor-waves-4-dark.png"
          alt=""
          width={1368}
          height={566}
          priority
          className="absolute top-[-40px] right-[-120px] w-[960px] max-w-none opacity-70 [mask-image:radial-gradient(60%_70%_at_65%_40%,black_30%,transparent_100%)] md:top-[-20px] md:right-[-80px]"
        />
        <div className="absolute inset-0 bg-[radial-gradient(80%_60%_at_20%_20%,#06000e_40%,transparent_100%)]" />
      </div>

      <div className="mx-auto grid w-full max-w-7xl items-center gap-14 px-6 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16 lg:px-8">
        <div className="max-w-2xl">
          <h1 className="text-[44px] leading-[1.02] font-semibold tracking-[-0.025em] text-foreground [text-wrap:balance] md:text-[72px] md:tracking-[-0.04em]">
            The framework for MCP
          </h1>
          <p className="mt-6 max-w-xl text-body-l leading-relaxed text-muted-foreground [text-wrap:pretty] md:text-[20px] md:leading-[1.5]">
            Build MCP servers, clients and interactive apps in ordinary Python. FastMCP derives
            the schemas, validates every call and handles transport and auth, so your code stays
            about your application.
          </p>

          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
            <ButtonLink
              href={docs("getting-started/quickstart")}
              size="lg"
              analytics={{ name: "hero_get_started", section: "hero" }}
            >
              Get started
            </ButtonLink>
            <ButtonLink
              href={docs("getting-started/welcome")}
              variant="secondary"
              size="lg"
              analytics={{ name: "hero_read_docs", section: "hero" }}
            >
              Read the docs
            </ButtonLink>
          </div>
          <CopyCommand command="uv add fastmcp" className="mt-5" />

          <dl className="mt-10 flex flex-wrap gap-x-7 gap-y-3">
            <Stat icon={<Star className="h-4 w-4" />} value={formatCount(stats.stars)} label="GitHub stars" href={GITHUB} />
            <Stat icon={<Download className="h-4 w-4" />} value={formatCount(stats.monthlyDownloads)} label="downloads a month" href={PYPI} />
            <Stat icon={<Server className="h-4 w-4" />} value="~70%" label="of MCP servers" />
          </dl>
        </div>

        <div className="min-w-0 space-y-3">
          <CodeBlock code={SERVER} language="python" filename="server.py" />
          <CodeBlock code={RUN} language="bash" className="[&_pre]:text-[12.5px]" />
        </div>
      </div>
    </section>
  );
}
