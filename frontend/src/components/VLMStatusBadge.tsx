"use client";

import { useEffect, useRef, useState } from "react";
import { useWSSubscribe } from "@/lib/ws";

interface Props {
  cameraId: string;
}

interface VLMState {
  status: "idle" | "queued" | "processing" | "slow" | "stalled" | string;
  avg_latency?: number;
  last_latency?: number;
}

/**
 * Per-tile VLM status pill. Subscribes via the shared WS context for
 * vlm_status events. Rendering rules.
 *
 *   idle        -> hidden
 *   queued      -> violet "Queued"
 *   processing  -> violet "Thinking"
 *   slow        -> amber "VLM slow (12.4s)"
 *   stalled     -> amber "VLM stalled"
 */
export function VLMStatusBadge({ cameraId }: Props) {
  const [state, setState] = useState<VLMState | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useWSSubscribe(
    "vlm_status",
    (msg) => {
      const v = (msg as { vlm?: VLMState }).vlm || ({} as VLMState);
      setState(v);
      if (idleTimer.current) clearTimeout(idleTimer.current);
      if (v.status && v.status !== "idle") {
        idleTimer.current = setTimeout(
          () => setState((s) => (s ? { ...s, status: "idle" } : s)),
          30000
        );
      }
    },
    cameraId
  );

  useEffect(
    () => () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
    },
    []
  );

  if (!state || state.status === "idle") return null;

  const isWarn = state.status === "slow" || state.status === "stalled";
  const colorDot = isWarn ? "bg-amber-400" : "bg-violet-400";
  const colorBorder = isWarn ? "border-amber-400/50" : "border-violet-400/50";
  const colorText = isWarn ? "text-amber-300" : "text-violet-300";
  const label =
    state.status === "stalled"
      ? "VLM stalled"
      : state.status === "slow"
        ? `VLM slow (${state.avg_latency?.toFixed(1)}s)`
        : state.status === "queued"
          ? "Queued"
          : "Thinking";

  return (
    <div
      role="status"
      aria-label={label}
      title={
        state.last_latency
          ? `${label} · last ${state.last_latency.toFixed(1)}s`
          : label
      }
      className={`flex items-center gap-1 rounded-full bg-black/60 backdrop-blur-sm px-1.5 py-0.5 border ${colorBorder}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        <span
          className={`absolute inline-flex h-full w-full animate-ping rounded-full ${colorDot} opacity-60`}
        />
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${colorDot}`} />
      </span>
      <span className={`text-[10px] uppercase tracking-wider ${colorText}`}>
        {label}
      </span>
    </div>
  );
}
