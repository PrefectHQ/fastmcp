"use client";

import Image from "next/image";
import { Menu, Star, X } from "lucide-react";
import { useEffect, useState } from "react";
import { BLOG_MCP, DOCS, GITHUB, docs, gatewaySignup } from "@/lib/links";
import { cn } from "@/lib/utils";
import { BrandIcon, ICONS } from "./brand-icons";
import { ButtonLink } from "./button";

const LINKS = [
  { label: "Docs", href: DOCS },
  { label: "Blog", href: BLOG_MCP, external: true },
] as const;

// Prefect's glass bar: Night at 55% under an 18px blur, a white 10% hairline,
// a 1344px shell inside 40px gutters that tighten on smaller screens.
export function Nav({ stars }: { stars: string }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header data-analytics-section="nav" className="fixed inset-x-0 top-0 z-50">
      <div
        aria-hidden
        className="absolute inset-0 border-b border-white/10 bg-[#06000e]/55 backdrop-blur-[18px]"
      />
      <div className="relative mx-auto flex h-(--nav-h) max-w-[1424px] items-center justify-between gap-4 pb-px pl-[max(40px,env(safe-area-inset-left))] pr-[max(40px,env(safe-area-inset-right))] max-[1240px]:pl-[max(24px,env(safe-area-inset-left))] max-[1240px]:pr-[max(24px,env(safe-area-inset-right))] max-[1150px]:pl-[max(16px,env(safe-area-inset-left))] max-[1150px]:pr-[max(16px,env(safe-area-inset-right))]">
        <a href="/" aria-label="FastMCP home" className="flex shrink-0 items-center">
          <Image
            src="/brand/fastmcp-logo-reverse.svg"
            alt=""
            width={128}
            height={28}
            priority
            className="h-7 w-auto"
          />
        </a>

        <nav className="hidden md:block" aria-label="Primary">
          <ul className="flex items-center gap-1">
            {LINKS.map((link) => (
              <li key={link.label}>
                <a
                  href={link.href}
                  {...("external" in link && link.external
                    ? { target: "_blank", rel: "noopener noreferrer" }
                    : {})}
                  className="flex items-center rounded-[8px] px-3 py-3 text-nav whitespace-nowrap text-white transition-colors hover:bg-white/10"
                >
                  {link.label}
                </a>
              </li>
            ))}
            <li>
              <a
                href={GITHUB}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-[8px] px-3 py-3 text-nav whitespace-nowrap text-white transition-colors hover:bg-white/10"
              >
                <BrandIcon icon={ICONS.github} className="h-4 w-4" />
                GitHub
                <span className="inline-flex items-center gap-1 rounded-full border border-white/16 px-2 py-0.5 font-mono text-[12px] text-white/70 tabular-nums">
                  <Star className="h-3 w-3" aria-hidden />
                  {stars}
                </span>
              </a>
            </li>
          </ul>
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <ButtonLink
            href={docs("getting-started/quickstart")}
            variant="secondary"
            className="hidden sm:inline-flex"
            analytics={{ name: "nav_get_started", section: "nav" }}
          >
            Get started
          </ButtonLink>
          <ButtonLink
            href={gatewaySignup("nav-deploy")}
            external
            analytics={{ name: "nav_deploy", section: "nav" }}
          >
            Deploy
          </ButtonLink>
          <button
            type="button"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            className="-mr-2.5 flex h-11 w-11 items-center justify-center rounded-md text-white/80 transition-colors hover:text-white md:hidden"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      <div
        className={cn(
          "absolute inset-x-0 top-(--nav-h) border-b border-white/10 bg-[#06000e]/95 backdrop-blur-[18px] md:hidden",
          open ? "block" : "hidden",
        )}
      >
        <nav aria-label="Primary, mobile" className="mx-auto max-w-[1424px] px-4 py-4">
          <ul className="flex flex-col">
            {LINKS.map((link) => (
              <li key={link.label}>
                <a
                  href={link.href}
                  {...("external" in link && link.external
                    ? { target: "_blank", rel: "noopener noreferrer" }
                    : {})}
                  className="block rounded-[8px] px-3 py-3 text-[16px] font-medium text-white hover:bg-white/10"
                >
                  {link.label}
                </a>
              </li>
            ))}
            <li>
              <a
                href={GITHUB}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-[8px] px-3 py-3 text-[16px] font-medium text-white hover:bg-white/10"
              >
                <BrandIcon icon={ICONS.github} className="h-4 w-4" />
                GitHub
                <span className="font-mono text-[12px] text-white/60 tabular-nums">{stars} stars</span>
              </a>
            </li>
            <li className="mt-2 border-t border-white/10 pt-4 sm:hidden">
              <ButtonLink
                href={docs("getting-started/quickstart")}
                variant="secondary"
                className="w-full"
                analytics={{ name: "nav_get_started", section: "nav" }}
              >
                Get started
              </ButtonLink>
            </li>
          </ul>
        </nav>
      </div>
    </header>
  );
}
