"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useWSSubscribe } from "@/lib/ws";

interface Visitor {
  name: string;
  sightings: number;
  first_seen?: string | null;
  last_seen?: string | null;
  cameras?: string[];
}

interface Facts {
  visitors?: Visitor[];
  unknown_visitors?: number;
  incidents_count?: number;
  journeys_count?: number;
  conversations_count?: number;
  packages?: number;
  vehicles?: number;
  audio_events?: Record<string, number>;
  audio_event_samples?: Record<string, string[]>;
  cameras_active?: { id: string; name: string; observations: number }[];
}

interface DailyDigest {
  id: string;
  window_start: string;
  window_end: string;
  generated_at: string;
  provider_name: string | null;
  summary_text: string | null;
  facts: Facts | null;
}

/**
 * Top-of-dashboard daily digest. Renders the last household-wide
 * morning summary from /api/daily-digest plus a structured bullet
 * list from the ``facts`` dict so the UI works even when the LLM
 * call returned empty.
 *
 * Updates live via the ``daily_digest_ready`` WS event so a manual
 * regen or the next scheduled run shows up without a refresh.
 */
export function DailyDigestCard() {
  const { authFetch } = useAuth();
  const [digest, setDigest] = useState<DailyDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await authFetch("/api/daily-digest");
      if (res.ok) {
        const data = await res.json();
        setDigest(data || null);
      }
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useWSSubscribe("daily_digest_ready", () => {
    refresh();
  });

  const runNow = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await authFetch("/api/daily-digest/run", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setDigest(data || null);
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading) return null;
  // No brief yet. show nothing rather than a placeholder. The morning brief
  // is configured in Settings → Morning digest (on by default at 7am), and
  // a real brief replaces this empty render once the scheduler runs or a
  // user generates one from Settings. Keeps the dashboard clean when empty.
  if (!digest) return null;

  const f = digest.facts || {};
  const bullets = buildBullets(f);
  const start = new Date(digest.window_start);
  const end = new Date(digest.window_end);

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
      >
        <SunIcon className="w-4 h-4 text-amber-400" />
        <span className="text-xs font-medium uppercase tracking-wider text-amber-300">
          Morning brief
        </span>
        <span className="text-[10px] text-muted-foreground font-mono">
          {start.toLocaleDateString()} {start.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})} → {end.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}
        </span>
        <ChevronIcon
          className={`ml-auto w-3.5 h-3.5 text-muted-foreground transition-transform ${
            collapsed ? "-rotate-90" : ""
          }`}
        />
      </button>
      {!collapsed && (
        <div className="px-3 pb-3 space-y-2">
          {digest.summary_text && (
            <p className="text-sm leading-relaxed text-foreground whitespace-pre-line">
              {digest.summary_text}
            </p>
          )}
          {bullets.length > 0 && (
            <ul className="text-xs space-y-1">
              {bullets.map((b, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="text-amber-400/60 mt-1 flex-shrink-0">•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2 pt-1 text-[10px] text-muted-foreground/70">
            <span>
              {digest.provider_name
                ? `narrated by ${digest.provider_name}`
                : "facts only (no LLM)"}
            </span>
            <span>·</span>
            <span>{new Date(digest.generated_at).toLocaleString()}</span>
            <button
              type="button"
              onClick={runNow}
              disabled={busy}
              className="ml-auto px-2 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {busy ? "Re-running." : "Regenerate"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function buildBullets(f: Facts): string[] {
  const out: string[] = [];
  const visitors = f.visitors || [];
  for (const v of visitors.slice(0, 5)) {
    const cams = (v.cameras || []).join(", ") || "?";
    out.push(`${v.name} seen ${v.sightings}× on ${cams}`);
  }
  if (f.unknown_visitors && f.unknown_visitors > 0) {
    out.push(`${f.unknown_visitors} unknown face sighting${f.unknown_visitors === 1 ? "" : "s"}`);
  }
  if (f.packages && f.packages > 0) {
    out.push(`${f.packages} package detection${f.packages === 1 ? "" : "s"}`);
  }
  if (f.vehicles && f.vehicles > 0) {
    out.push(`${f.vehicles} vehicle detection${f.vehicles === 1 ? "" : "s"}`);
  }
  if (f.incidents_count) {
    out.push(`${f.incidents_count} incidents tracked`);
  }
  if (f.journeys_count) {
    out.push(`${f.journeys_count} cross-camera journeys`);
  }
  const audio = f.audio_events || {};
  const samples = f.audio_event_samples || {};
  for (const [label, n] of Object.entries(audio)) {
    if (!n) continue;
    const labelStr = label.replace(/_/g, " ");
    const firstSample = (samples[label] || [])[0];
    const tail = firstSample ? ` (first ${formatSample(firstSample)})` : "";
    out.push(`${labelStr} detected ${n}×${tail}`);
  }
  return out;
}

function formatSample(s: string): string {
  // Sample shape "ISO@CamName". Show time + cam concisely.
  const [iso, cam] = s.split("@");
  try {
    const d = new Date(iso);
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return cam ? `${t} on ${cam}` : t;
  } catch {
    return s;
  }
}

function SunIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}
