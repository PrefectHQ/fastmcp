import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

// Page rhythm from the Prefect design system: 96 to 128px between sections,
// a 1280px shell inside 24px gutters, hairline dividers instead of panels.
export function Section({
  className,
  children,
  bordered = false,
  name,
  ...rest
}: ComponentProps<"section"> & { bordered?: boolean; name: string }) {
  return (
    <section
      data-analytics-section={name}
      className={cn("py-24 md:py-32", bordered && "border-t border-white/10", className)}
      {...rest}
    >
      <div className="mx-auto w-full max-w-7xl px-6 lg:px-8">{children}</div>
    </section>
  );
}

export function SectionHeading({
  title,
  lede,
  className,
  align = "left",
}: {
  title: ReactNode;
  lede?: ReactNode;
  className?: string;
  align?: "left" | "center";
}) {
  return (
    <div className={cn("max-w-3xl", align === "center" && "mx-auto text-center", className)}>
      <h2 className="text-h5 text-foreground [text-wrap:balance] md:text-h3">{title}</h2>
      {lede && (
        <p className="mt-5 text-body-l leading-relaxed text-muted-foreground [text-wrap:pretty]">
          {lede}
        </p>
      )}
    </div>
  );
}
