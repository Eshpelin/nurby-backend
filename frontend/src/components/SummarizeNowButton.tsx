"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";

interface Props {
  cameraId: string;
  windowMinutes?: number;
  // Show the wide tile-overlay variant (icon + text) vs a compact pill.
  variant?: "tile" | "compact";
}

const SparkleIcon = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    <circle cx="12" cy="12" r="2" />
  </svg>
);

/**
 * Triggers POST /api/summaries/run for the camera. Used both as a tile
 * overlay control and as a button inside camera detail pages so the
 * user can force a recap on demand instead of waiting for the next
 * periodic tick.
 */
export function SummarizeNowButton({
  cameraId,
  windowMinutes = 30,
  variant = "tile",
}: Props) {
  const { authFetch } = useAuth();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (busy) return;
    setBusy(true);
    setErr(null);
    setDone(false);
    try {
      const res = await authFetch("/api/summaries/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ camera_id: cameraId, window_minutes: windowMinutes }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `status ${res.status}`);
      }
      setDone(true);
      setTimeout(() => setDone(false), 2500);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "failed");
      setTimeout(() => setErr(null), 4000);
    } finally {
      setBusy(false);
    }
  };

  if (variant === "compact") {
    return (
      <button
        type="button"
        onClick={run}
        disabled={busy}
        title={err || (done ? "Summary generated" : `Summarize last ${windowMinutes} min`)}
        className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50"
      >
        <SparkleIcon className="w-3 h-3" />
        {busy ? "..." : done ? "✓" : "Summarize"}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={run}
      disabled={busy}
      title={
        err
          ? `Summarize failed: ${err}`
          : done
            ? "Summary generated. Check the timeline."
            : `Summarize last ${windowMinutes} min`
      }
      className={`absolute top-1.5 right-[5.5rem] z-10 w-6 h-6 rounded-md bg-black/60 backdrop-blur-sm border flex items-center justify-center text-white/70 hover:text-white hover:bg-black/80 transition-colors opacity-0 group-hover:opacity-100 ${
        err
          ? "border-danger/50 text-danger"
          : done
            ? "border-emerald-400/50 text-emerald-300"
            : "border-white/10"
      }`}
    >
      {busy ? (
        <svg className="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="40 60" />
        </svg>
      ) : (
        <SparkleIcon className="w-3 h-3" />
      )}
    </button>
  );
}
