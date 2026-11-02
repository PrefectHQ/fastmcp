import Image from "next/image";
import { ButtonLink } from "@/components/button";
import { docs, gatewaySignup, prefect } from "@/lib/links";

export function DeployCta() {
  return (
    <section className="relative overflow-hidden border-t border-white/10 py-28 md:py-36">
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute inset-x-0 bottom-0 h-[60%] bg-[radial-gradient(70%_80%_at_50%_100%,rgba(254,150,85,0.22)_0%,rgba(32,132,171,0.12)_45%,transparent_100%)]" />
        <div className="absolute inset-x-0 bottom-0 h-px bg-[linear-gradient(90deg,transparent,rgba(250,205,150,0.6)_30%,rgba(138,222,255,0.6)_70%,transparent)]" />
      </div>
      <div className="mx-auto w-full max-w-7xl px-6 text-center lg:px-8">
        <h2 className="mx-auto max-w-3xl text-h4 text-foreground [text-wrap:balance] md:text-h2">
          Your server is written. Give it a URL.
        </h2>
        <p className="mx-auto mt-6 max-w-2xl text-body-l leading-relaxed text-muted-foreground [text-wrap:pretty]">
          Push a FastMCP server from GitHub and FastMCP Gateway gives you a live, OAuth-protected
          endpoint in about a minute. Free for personal projects. The private registry, tool-level
          access control and audit trail are there when a team needs them.
        </p>
        <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <ButtonLink href={gatewaySignup("final-cta")} size="lg" external>
            Deploy for free
          </ButtonLink>
          <ButtonLink href={docs("deployment/fastmcp-gateway")} variant="secondary" size="lg">
            Read the deployment guide
          </ButtonLink>
        </div>
        <a
          href={prefect("/fastmcp", "final-cta")}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-8 inline-flex items-center gap-2 text-body-s text-white/60 transition-colors hover:text-white"
        >
          <Image src="/brand/prefect-logomark-white.svg" alt="" width={11} height={15} className="h-[15px] w-auto opacity-80" />
          FastMCP and FastMCP Gateway are built by the same team at Prefect
        </a>
      </div>
    </section>
  );
}
