"use client";

import { useState } from "react";

interface Props {
  primaryText: string;
  refinedText: string;
  refinerProviderName: string;
}

const Sparkle = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    <circle cx="12" cy="12" r="2" />
  </svg>
);

/**
 * Inline pill that marks an observation whose description was
 * upgraded by the cascade refiner. Click expands a side-by-side
 * popover showing the cheap-model output above the refined text so
 * the user can see what the upgrade actually changed.
 */
export function RefinedBadge({
  primaryText,
  refinedText,
  refinerProviderName,
}: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30 text-[10px] uppercase tracking-wider hover:bg-sky-500/25 transition-colors"
        title="Click to compare with the original primary VLM output"
      >
        <Sparkle className="w-3 h-3" />
        Refined by {refinerProviderName}
        <svg
          className={`w-2.5 h-2.5 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
          <div className="rounded border border-border bg-card/40 p-2">
            <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1">
              Primary
            </div>
            <p className="leading-relaxed text-muted-foreground">
              {primaryText}
            </p>
          </div>
          <div className="rounded border border-sky-500/40 bg-sky-500/5 p-2">
            <div className="text-[9px] uppercase tracking-wider text-sky-300 mb-1 flex items-center gap-1">
              <Sparkle className="w-2.5 h-2.5" />
              Refined · {refinerProviderName}
            </div>
            <p className="leading-relaxed text-foreground">
              {refinedText}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
