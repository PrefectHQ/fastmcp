"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

// The install line under the hero headline: a mono command with a copy
// button, the same affordance the Next.js homepage gives its create command.
export function CopyCommand({ command, className }: { command: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be denied; the text is still selectable.
    }
  }

  return (
    <div
      className={cn(
        "inline-flex h-12 items-center gap-3 rounded-[6px] border border-white/16 bg-white/[0.04] pl-4 pr-2 font-mono text-[14px] text-white/90 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]",
        className,
      )}
    >
      <span aria-hidden className="text-white/40 select-none">
        $
      </span>
      <code className="select-all">{command}</code>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? "Copied" : `Copy ${command}`}
        className="ml-1 flex h-8 w-8 items-center justify-center rounded-[4px] text-white/60 transition-colors hover:bg-white/10 hover:text-white"
      >
        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  );
}
