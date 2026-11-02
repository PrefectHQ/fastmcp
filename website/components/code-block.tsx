import { codeToHtml } from "shiki";
import { cn } from "@/lib/utils";

// Server-rendered Shiki block, the same GitHub Dark theme and chrome the
// Prefect site uses for its code samples.
export async function CodeBlock({
  code,
  language = "python",
  filename,
  className,
}: {
  code: string;
  language?: string;
  filename?: string;
  className?: string;
}) {
  const html = await codeToHtml(code.trimEnd(), { lang: language, theme: "github-dark" });
  const inner = html.match(/<code[^>]*>(.*?)<\/code>/s)?.[1] ?? code;

  return (
    <div
      className={cn(
        "min-w-0 overflow-hidden rounded-xl border border-white/10 bg-code-bg shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]",
        className,
      )}
    >
      {filename && (
        <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5 font-mono text-[12px] text-white/50">
          <span className="h-2 w-2 rounded-full bg-white/20" aria-hidden />
          {filename}
        </div>
      )}
      <pre
        tabIndex={0}
        className="overflow-x-auto p-5 font-mono text-[13.5px] leading-[1.65] text-code-text"
      >
        <code dangerouslySetInnerHTML={{ __html: inner }} />
      </pre>
    </div>
  );
}
