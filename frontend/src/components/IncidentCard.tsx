"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useWSSubscribe } from "@/lib/ws";
import { ReinterpretButton } from "@/components/ReinterpretButton";

interface IncidentObs {
  id: string;
  started_at: string;
  vlm_description: string | null;
  thumbnail_path: string | null;
  refined_by_provider_name?: string | null;
  primary_vlm_description?: string | null;
}

interface Incident {
  id: string;
  camera_id: string;
  signature_kind: string;
  signature_key: string;
  started_at: string;
  last_seen_at: string;
  ended_at: string | null;
  finalized: boolean;
  occurrence_count: number;
  peak_observation_id: string | null;
  observation_ids: string[] | null;
  thumbnails: { obs_id: string; path: string | null; ts: string }[] | null;
  summary_text: string | null;
  summary_provider_name: string | null;
}

interface Props {
  incident: Incident;
  cameraName?: string;
}

const RepeatIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polyline points="17 1 21 5 17 9" />
    <path d="M3 11V9a4 4 0 0 1 4-4h14" />
    <polyline points="7 23 3 19 7 15" />
    <path d="M21 13v2a4 4 0 0 1-4 4H3" />
  </svg>
);

const ChevronDown = ({ className }: { className?: string }) => (
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
 * Persistent incident card. Renders /api/incidents rows. Subscribes
 * to incident_updated and incident_finalized so the count and
 * summary appear in real time. Click to expand and lazy-load the
 * full observation list with descriptions.
 */
export function IncidentCard({ incident, cameraName }: Props) {
  const { token, authFetch } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [obs, setObs] = useState<IncidentObs[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState({
    occurrence_count: incident.occurrence_count,
    last_seen_at: incident.last_seen_at,
    finalized: incident.finalized,
    summary_text: incident.summary_text,
    summary_provider_name: incident.summary_provider_name,
  });

  useEffect(() => {
    setLive({
      occurrence_count: incident.occurrence_count,
      last_seen_at: incident.last_seen_at,
      finalized: incident.finalized,
      summary_text: incident.summary_text,
      summary_provider_name: incident.summary_provider_name,
    });
  }, [
    incident.occurrence_count,
    incident.last_seen_at,
    incident.finalized,
    incident.summary_text,
    incident.summary_provider_name,
  ]);

  useWSSubscribe(
    ["incident_updated", "incident_finalized"],
    (msg) => {
      if (msg.incident_id !== incident.id) return;
      if (msg.type === "incident_updated") {
        setLive((s) => ({
          ...s,
          occurrence_count: Number(msg.occurrence_count) || s.occurrence_count,
          last_seen_at: String(msg.last_seen_at) || s.last_seen_at,
        }));
      }
      if (msg.type === "incident_finalized") {
        setLive((s) => ({
          ...s,
          finalized: true,
          summary_text:
            (typeof msg.summary_text === "string" ? msg.summary_text : null) ||
            s.summary_text,
        }));
      }
    },
    incident.camera_id
  );

  const span = Math.max(
    0,
    Math.round(
      (new Date(live.last_seen_at).getTime() -
        new Date(incident.started_at).getTime()) /
        1000
    )
  );
  const spanLabel =
    span < 60
      ? `${span}s`
      : span < 3600
        ? `${Math.round(span / 60)}m`
        : `${(span / 3600).toFixed(1)}h`;

  const headline = formatSignature(incident.signature_kind, incident.signature_key);

  const tone = live.finalized
    ? "border-emerald-700/40 bg-emerald-950/15 hover:border-emerald-600/60"
    : "border-violet-700/40 bg-violet-950/15 hover:border-violet-600/60";
  const accent = live.finalized ? "text-emerald-300" : "text-violet-300";
  const accentDot = live.finalized ? "text-emerald-400" : "text-violet-400";

  async function loadObservations() {
    if (obs !== null || loading) return;
    setLoading(true);
    try {
      const res = await authFetch(`/api/incidents/${incident.id}`);
      if (res.ok) {
        const data = await res.json();
        setObs(data.observations || []);
      }
    } finally {
      setLoading(false);
    }
  }

  function toggle() {
    setExpanded((v) => {
      const next = !v;
      if (next) loadObservations();
      return next;
    });
  }

  const peakThumbObs = incident.peak_observation_id;
  const thumbId = peakThumbObs ?? (incident.thumbnails?.[0]?.obs_id ?? null);

  return (
    <div className={`rounded-lg border ${tone} overflow-hidden transition-colors`}>
      <button type="button" onClick={toggle} className="w-full text-left px-3 py-2.5">
        <div className="flex items-center gap-2 text-[11px] mb-1.5">
          <RepeatIcon className={`w-3.5 h-3.5 ${accentDot}`} />
          <span className={`font-medium uppercase tracking-wider ${accent}`}>
            {live.finalized ? "Incident closed" : "Incident · live"}
          </span>
          {!live.finalized && (
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-violet-400" />
            </span>
          )}
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">{cameraName || "Camera"}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground font-mono">
            {live.occurrence_count}× over {spanLabel}
          </span>
          <ChevronDown
            className={`ml-auto w-3.5 h-3.5 text-muted-foreground transition-transform ${
              expanded ? "rotate-180" : ""
            }`}
          />
        </div>
        <div className="flex gap-3">
          {thumbId && (
            <div className="w-20 h-14 flex-shrink-0 bg-black/50 rounded overflow-hidden">
              <img
                src={`/api/observations/${thumbId}/thumbnail${
                  token ? `?token=${token}` : ""
                }`}
                alt=""
                className="w-full h-full object-cover"
              />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium leading-snug">
              {headline}
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                seen {live.occurrence_count}×
              </span>
            </p>
            {live.finalized && live.summary_text ? (
              <p className="mt-1 text-xs text-foreground leading-relaxed">
                <Sparkle className="inline-block w-3 h-3 mr-1 text-emerald-400" />
                {live.summary_text}
              </p>
            ) : null}
            <div className="mt-1 flex items-center gap-1 flex-wrap">
              {(incident.thumbnails || []).slice(-8).map((t) => (
                <span
                  key={t.obs_id}
                  className={`text-[10px] font-mono ${accent}/80 px-1 py-0.5 rounded bg-violet-500/10`}
                  title={new Date(t.ts).toLocaleString()}
                >
                  {new Date(t.ts).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              ))}
            </div>
            {live.finalized && live.summary_provider_name && (
              <p className="mt-1 text-[10px] text-muted-foreground/70">
                summary by {live.summary_provider_name}
              </p>
            )}
            {live.finalized && (
              <div className="mt-2">
                <ReinterpretButton
                  endpoint={`/api/incidents/${incident.id}/reinterpret`}
                  label="Reinterpret"
                  variant="compact"
                />
              </div>
            )}
          </div>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-border/40 bg-black/20 px-3 py-2.5 space-y-2">
          {loading && obs === null ? (
            <p className="text-xs text-muted-foreground">Loading occurrences.</p>
          ) : obs && obs.length > 0 ? (
            obs.map((o) => (
              <div
                key={o.id}
                className="rounded border border-border/50 bg-card/40 p-2 text-xs"
              >
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground mb-1">
                  <span className="font-mono">
                    {new Date(o.started_at).toLocaleTimeString()}
                  </span>
                  {o.refined_by_provider_name && (
                    <span className="text-sky-300">✨ refined</span>
                  )}
                </div>
                {o.vlm_description ? (
                  <p className="leading-relaxed">{o.vlm_description}</p>
                ) : (
                  <p className="text-muted-foreground italic">
                    (no description)
                  </p>
                )}
              </div>
            ))
          ) : (
            <p className="text-xs text-muted-foreground">No occurrences recorded.</p>
          )}
        </div>
      )}
    </div>
  );
}

function formatSignature(kind: string, key: string): string {
  if (kind === "person") return key;
  if (kind === "cluster") {
    const short = key.split(",")[0]?.slice(0, 8) ?? "stranger";
    return `Recurring stranger ${short}`;
  }
  if (kind === "unknown") return "Unknown person";
  if (kind === "object") {
    const labels = key.split(",");
    if (labels.length === 1) return capitalize(labels[0]);
    if (labels.length === 2) return `${capitalize(labels[0])} + ${labels[1]}`;
    return `${capitalize(labels[0])} + ${labels.length - 1} more`;
  }
  return "Motion";
}

function capitalize(s: string): string {
  return s.length === 0 ? s : s[0].toUpperCase() + s.slice(1);
}
