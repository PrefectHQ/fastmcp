import type { ComponentProps, ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Prefect's two button treatments: solid white (primary) and a white 32%
// hairline (secondary). Neither is filled with a brand color.
const VARIANTS = {
  primary:
    "border border-white bg-white text-[#06000e] hover:bg-white/90 focus-visible:ring-2 focus-visible:ring-white/60",
  secondary:
    "border border-white/32 text-white hover:border-white/60 hover:bg-white/5 focus-visible:ring-2 focus-visible:ring-white/60",
} as const;

const SIZES = {
  default: "h-10 px-4",
  lg: "h-12 px-6 text-[15px]",
} as const;

type ButtonLinkProps = Omit<ComponentProps<"a">, "href"> & {
  href: string;
  variant?: keyof typeof VARIANTS;
  size?: keyof typeof SIZES;
  /** Leaves gofastmcp.com: opens in a new tab and shows the arrow. */
  external?: boolean;
  children: ReactNode;
};

export function ButtonLink({
  href,
  variant = "primary",
  size = "default",
  external = false,
  className,
  children,
  ...rest
}: ButtonLinkProps) {
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-[4px] text-button whitespace-nowrap transition-colors focus-visible:outline-none",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {children}
      {external && <ArrowUpRight className="h-4 w-4 opacity-70" aria-hidden />}
    </a>
  );
}
