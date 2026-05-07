"use client";

import { useEffect, useState } from "react";
import { useWSSubscribe } from "@/lib/ws";

interface Props {
  cameraId: string;
  // Hardware FPS as configured / measured by ingestion.
  fps?: number | null;
  // Frame width/height for the resolution line.
  width?: number | null;
  height?: number | null;
}

interface VLMStats {
  status: string;
  avg_latency: number;
  last_latency: number;
  total_calls: number;
  total_errors: number;
  total_dropped: number;
}

/**
 * Compact hover popover that surfaces per-camera throughput stats.
 * Mounts on each tile via the same overlay layer as VLMStatusBadge.
 * Subscribes to vlm_status WS messages so the readout stays live
 * without polling.
 *
 * Keep narrow. The user wants quick visibility on FPS, VLM latency,
 * queue depth, and dropped frames so they can spot a struggling
 * camera at a glance. This is not the place for a full debug panel.
 */
export function CameraStatsHover({ cameraId, fps, width, height }: Props) {
  const [stats, setStats] = useState<VLMStats | null>(null);

  useWSSubscribe(
    "vlm_status",
    (msg) => {
      const v = (msg as { vlm?: VLMStats }).vlm;
      if (v) setStats(v);
    },
    cameraId
  );

  // Initial pull so the popover has something even before the first
  // WS event lands. /api/system/vlm-stats returns a per-camera dict.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/system/vlm-stats", {
          credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        const mine = data?.[cameraId];
        if (mine) setStats(mine);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cameraId]);

  return (
    <div
      className="absolute top-1.5 right-[8.5rem] z-10 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
    >
      <div className="rounded-md bg-black/75 backdrop-blur-sm border border-white/10 px-2 py-1.5 text-[10px] font-mono text-white/80 space-y-0.5 min-w-[7.5rem]">
        {fps != null && (
          <Row label="FPS" value={fps.toFixed(1)} />
        )}
        {width != null && height != null && (
          <Row label="RES" value={`${width}x${height}`} />
        )}
        {stats && (
          <>
            <Row
              label="VLM"
              value={
                stats.status === "idle"
                  ? "idle"
                  : `${stats.last_latency.toFixed(1)}s`
              }
              tone={
                stats.status === "slow" || stats.status === "stalled"
                  ? "warn"
                  : undefined
              }
            />
            {stats.total_dropped > 0 && (
              <Row
                label="DROP"
                value={String(stats.total_dropped)}
                tone="warn"
              />
            )}
            {stats.total_errors > 0 && (
              <Row
                label="ERR"
                value={String(stats.total_errors)}
                tone="danger"
              />
            )}
            <Row label="CALLS" value={String(stats.total_calls)} />
          </>
        )}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn" | "danger";
}) {
  const v =
    tone === "danger"
      ? "text-danger"
      : tone === "warn"
        ? "text-warning"
        : "text-white/80";
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-white/40 text-[9px] uppercase tracking-wider">
        {label}
      </span>
      <span className={v}>{value}</span>
    </div>
  );
}
