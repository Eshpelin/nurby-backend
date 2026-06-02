"use client";

// Brand-aware "how do I find my camera's connection details?" helper.
// Pick a brand. Get the RTSP URL template(s) and the exact steps to
// enable RTSP/ONVIF and find credentials. "Use this URL" drops the
// template into the stream-URL field so the user only swaps <ip>.

import { useState } from "react";
import { CAMERA_BRANDS, findBrand, type RtspSupport } from "@/lib/camera-brands";

interface Props {
  // Called when the user clicks "Use this URL" on a template.
  onUseTemplate: (url: string) => void;
  // Optional. Start collapsed unless the user opens it.
  defaultOpen?: boolean;
}

const SUPPORT_BADGE: Record<RtspSupport, { label: string; cls: string }> = {
  yes: { label: "RTSP supported", cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  limited: { label: "Limited / extra setup", cls: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  no: { label: "Cloud-locked", cls: "bg-rose-500/15 text-rose-400 border-rose-500/30" },
};

export default function CameraBrandHelp({ onUseTemplate, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const [brandId, setBrandId] = useState<string>("");
  const brand = brandId ? findBrand(brandId) : undefined;

  return (
    <div className="rounded-md border border-border bg-background/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span className="text-xs font-medium flex items-center gap-1.5">
          <span>🎥</span> Don&apos;t know your camera&apos;s URL? Pick your brand
        </span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-border/60 pt-3">
          {/* Brand chips */}
          <div className="flex flex-wrap gap-1.5">
            {CAMERA_BRANDS.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => setBrandId(b.id === brandId ? "" : b.id)}
                className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${
                  brandId === b.id
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border bg-background hover:border-accent/40"
                }`}
              >
                {b.name}
              </button>
            ))}
          </div>

          {!brand && (
            <p className="text-[11px] text-muted-foreground">
              Choose your camera maker for step-by-step instructions. Not
              listed? Try <span className="text-foreground">Generic ONVIF</span>{" "}
              or the Scan tab.
            </p>
          )}

          {brand && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold">{brand.name}</span>
                <span
                  className={`text-[9px] px-1.5 py-px rounded border ${SUPPORT_BADGE[brand.support].cls}`}
                >
                  {SUPPORT_BADGE[brand.support].label}
                </span>
                {brand.port && (
                  <span className="text-[10px] text-muted-foreground font-mono">
                    port {brand.port}
                  </span>
                )}
              </div>

              {/* Steps */}
              <ol className="space-y-1.5">
                {brand.steps.map((s, i) => (
                  <li key={i} className="flex gap-2 text-[11px] text-muted-foreground leading-snug">
                    <span className="shrink-0 w-4 h-4 rounded-full bg-accent/15 text-accent text-[9px] flex items-center justify-center font-medium">
                      {i + 1}
                    </span>
                    <span>{s}</span>
                  </li>
                ))}
              </ol>

              {/* Templates */}
              {brand.templates.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    Stream URL{brand.templates.length > 1 ? "s" : ""}
                  </div>
                  {brand.templates.map((t) => (
                    <div
                      key={t.label}
                      className="flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] text-muted-foreground">{t.label}</div>
                        <div className="text-[11px] font-mono truncate">{t.url}</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => onUseTemplate(t.url)}
                        className="shrink-0 px-2 py-1 text-[10px] rounded bg-foreground text-background font-medium hover:opacity-90"
                      >
                        Use this URL
                      </button>
                    </div>
                  ))}
                  <p className="text-[10px] text-muted-foreground">
                    Replace <span className="font-mono">&lt;ip&gt;</span> with your
                    camera&apos;s IP. You can leave{" "}
                    <span className="font-mono">&lt;user&gt;:&lt;pass&gt;@</span> in the
                    URL, or remove it and use the Credentials section below.
                  </p>
                </div>
              )}

              {/* Notes */}
              {brand.notes?.map((n, i) => (
                <div
                  key={i}
                  className="text-[10px] text-amber-300/90 bg-amber-500/5 border border-amber-500/20 rounded px-2 py-1.5 leading-snug"
                >
                  {n}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
