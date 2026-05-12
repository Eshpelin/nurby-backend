"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useWSSubscribe } from "@/lib/ws";
import { ReinterpretButton } from "@/components/ReinterpretButton";

interface Segment {
  camera_id: string;
  camera_name: string | null;
  location_label: string | null;
  incident_id: string | null;
  started_at: string;
  last_seen_at: string;
  occurrence_count: number;
  peak_observation_id: string | null;
}

interface Transition {
  from_camera_id: string;
  from_camera_name: string | null;
  to_camera_id: string;
  to_camera_name: string | null;
  gap_seconds: number;
  ts: string;
}

export interface Journey {
  id: string;
  subject_kind: "person" | "cluster" | string;
  subject_key: string;
  started_at: string;
  last_seen_at: string;
  ended_at: string | null;
  finalized: boolean;
  segments: Segment[];
  transitions: Transition[];
  cameras_seen_count: number;
  incidents_count: number;
  summary_text: string | null;
  summary_provider_name: string | null;
}

interface Props {
  journey: Journey;
}

/**
 * Multi-camera story card. Renders a horizontal path of camera
 * segments connected by arrows that show transition gaps. Live
 * journeys pulse violet; finalized journeys flip to emerald and
 * surface a VLM-generated narrative on top.
 */
export function JourneyCard({ journey }: Props) {
  const { token } = useAuth();
  const [live, setLive] = useState({
    last_seen_at: journey.last_seen_at,
    cameras_seen_count: journey.cameras_seen_count,
    incidents_count: journey.incidents_count,
    finalized: journey.finalized,
    summary_text: journey.summary_text,
  });

  useEffect(() => {
    setLive({
      last_seen_at: journey.last_seen_at,
      cameras_seen_count: journey.cameras_seen_count,
      incidents_count: journey.incidents_count,
      finalized: journey.finalized,
      summary_text: journey.summary_text,
    });
  }, [journey]);

  useWSSubscribe(["journey_updated", "journey_finalized"], (msg) => {
    if (msg.journey_id !== journey.id) return;
    if (msg.type === "journey_updated") {
      setLive((s) => ({
        ...s,
        last_seen_at: String(msg.last_seen_at) || s.last_seen_at,
        cameras_seen_count: Number(msg.cameras_seen_count) || s.cameras_seen_count,
        incidents_count: Number(msg.incidents_count) || s.incidents_count,
      }));
    } else if (msg.type === "journey_finalized") {
      setLive((s) => ({
        ...s,
        finalized: true,
        summary_text:
          (typeof msg.summary_text === "string" ? msg.summary_text : null) ||
          s.summary_text,
      }));
    }
  });

  const start = new Date(journey.started_at);
  const end = new Date(live.last_seen_at);
  const durationS = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
  const durationLabel =
    durationS < 60
      ? `${durationS}s`
      : durationS < 3600
        ? `${Math.round(durationS / 60)}m`
        : `${(durationS / 3600).toFixed(1)}h`;

  const subjectLabel =
    journey.subject_kind === "person"
      ? journey.subject_key
      : `Recurring stranger ${journey.subject_key.slice(0, 8)}`;

  const tone = live.finalized
    ? "border-emerald-700/40 bg-emerald-950/15 hover:border-emerald-600/60"
    : "border-violet-700/40 bg-violet-950/15 hover:border-violet-600/60";
  const accent = live.finalized ? "text-emerald-300" : "text-violet-300";
  const accentDot = live.finalized ? "text-emerald-400" : "text-violet-400";

  return (
    <div className={`rounded-lg border ${tone} overflow-hidden transition-colors`}>
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-2 text-[11px] mb-1.5">
          <PathIcon className={`w-3.5 h-3.5 ${accentDot}`} />
          <span className={`font-medium uppercase tracking-wider ${accent}`}>
            {live.finalized ? "Journey closed" : "Journey · live"}
          </span>
          {!live.finalized && (
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-violet-400" />
            </span>
          )}
          <span className="text-muted-foreground">·</span>
          <span className="text-foreground font-medium">{subjectLabel}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground font-mono">
            {live.cameras_seen_count} cameras · {durationLabel}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {live.finalized && (
              <ReinterpretButton
                endpoint={`/api/journeys/${journey.id}/reinterpret`}
                label="Reinterpret"
                variant="compact"
              />
            )}
            {journey.subject_kind === "person" && (
              <Link
                href={`/follow/person/${encodeURIComponent(journey.subject_key)}`}
                onClick={(e) => e.stopPropagation()}
                className="text-[10px] text-accent hover:underline"
                title="Follow this person across all time"
              >
                follow ↗
              </Link>
            )}
          </div>
        </div>

        {live.finalized && live.summary_text && (
          <p className="text-sm text-foreground leading-relaxed mb-2">
            <SparkleIcon className="inline-block w-3 h-3 mr-1 text-emerald-400" />
            {live.summary_text}
          </p>
        )}

        {/* Path strip */}
        <div className="flex items-stretch gap-1.5 overflow-x-auto pb-1">
          {journey.segments.map((s, i) => (
            <div
              key={`${s.camera_id}-${i}`}
              className="flex items-stretch gap-1.5 flex-shrink-0"
            >
              <SegmentChip segment={s} token={token} accent={accent} />
              {i < journey.segments.length - 1 && (
                <TransitionArrow
                  transition={journey.transitions[i] || null}
                />
              )}
            </div>
          ))}
        </div>

        {live.finalized && live.summary_text === null && (
          <p className="text-[10px] text-muted-foreground/70 mt-1">
            (single-camera journey, no narrative)
          </p>
        )}
      </div>
    </div>
  );
}

function SegmentChip({
  segment,
  token,
  accent,
}: {
  segment: Segment;
  token: string | null;
  accent: string;
}) {
  const thumb = segment.peak_observation_id;
  const t = new Date(segment.started_at);
  const tStr = t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="flex flex-col items-center gap-1 min-w-[6.5rem]">
      <div className="relative w-24 h-14 bg-black/50 rounded overflow-hidden border border-border/50">
        {thumb && token ? (
          <img
            src={`/api/observations/${thumb}/thumbnail?token=${encodeURIComponent(token)}`}
            alt=""
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-[9px] text-muted-foreground font-mono">
            no thumb
          </div>
        )}
      </div>
      <div className="text-center">
        <div className={`text-[10px] font-medium ${accent} truncate max-w-[7rem]`}>
          {segment.camera_name || "Camera"}
        </div>
        <div className="text-[9px] font-mono text-muted-foreground">
          {tStr} · {segment.occurrence_count}×
        </div>
      </div>
    </div>
  );
}

function TransitionArrow({ transition }: { transition: Transition | null }) {
  if (!transition) {
    return (
      <div className="flex items-center text-muted-foreground/50">
        <ArrowIcon className="w-4 h-4" />
      </div>
    );
  }
  const gap = transition.gap_seconds;
  const gapStr = gap < 60 ? `${gap}s` : `${Math.round(gap / 60)}m`;
  const warn = gap > 120;
  return (
    <div
      className={`flex flex-col items-center justify-center px-1 ${
        warn ? "text-amber-300" : "text-muted-foreground"
      }`}
      title={`Off-camera ${gapStr}`}
    >
      <ArrowIcon className="w-4 h-4" />
      <span className="text-[9px] font-mono">{gapStr}</span>
    </div>
  );
}

function PathIcon({ className }: { className?: string }) {
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
      <circle cx="5" cy="6" r="2.5" />
      <circle cx="19" cy="18" r="2.5" />
      <path d="M7 6 Q12 6 12 12 T17 18" />
    </svg>
  );
}

function ArrowIcon({ className }: { className?: string }) {
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
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points="13 6 19 12 13 18" />
    </svg>
  );
}

function SparkleIcon({ className }: { className?: string }) {
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
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  );
}
