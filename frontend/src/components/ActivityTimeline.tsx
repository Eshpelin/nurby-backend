"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Segment {
  id: string;
  person_id?: string | null;
  person_name?: string | null;
  track_id?: number | null;
  action: string;
  started_at?: string | null;
  ended_at?: string | null;
  zone?: string | null;
}

const ACTION_COLOR: Record<string, string> = {
  fallen: "bg-red-500/30 text-red-200 border-red-500/40",
  eating: "bg-emerald-500/20 text-emerald-200 border-emerald-500/30",
  drinking: "bg-emerald-500/20 text-emerald-200 border-emerald-500/30",
  walking: "bg-sky-500/20 text-sky-200 border-sky-500/30",
  sitting: "bg-zinc-500/20 text-zinc-200 border-zinc-500/30",
  lying_down: "bg-amber-500/20 text-amber-100 border-amber-500/30",
  sleeping: "bg-indigo-500/20 text-indigo-200 border-indigo-500/30",
};

function fmt(ts?: string | null): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

/**
 * HAR activity timeline for one camera. Lists merged per-person action segments over the
 * last 24h from GET /api/cameras/{id}/actions. Empty (renders a hint) until HAR is enabled
 * and producing segments. Operator-facing; identity is shown as resolved server-side.
 */
export function ActivityTimeline({ cameraId }: { cameraId: string }) {
  const { authFetch } = useAuth();
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`/api/cameras/${cameraId}/actions?hours=24`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setSegments(Array.isArray(data.items) ? data.items : []);
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cameraId, authFetch]);

  if (loading) return null;

  return (
    <section className="rounded-lg border border-[hsl(0_0%_14.9%)] bg-[hsl(0_0%_5.5%)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">Activity timeline</h3>
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          last 24h
        </span>
      </div>
      {segments.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No actions recorded. Human action recognition is off or has not produced segments
          for this camera yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {segments.map((s) => (
            <li key={s.id} className="flex items-center gap-2 text-xs">
              <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">
                {fmt(s.started_at)}
              </span>
              <span
                className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${
                  ACTION_COLOR[s.action] || "bg-white/5 text-white/80 border-white/10"
                }`}
              >
                {s.action}
              </span>
              <span className="truncate text-muted-foreground">
                {s.person_name || (s.person_id ? "Known person" : "Unidentified")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
