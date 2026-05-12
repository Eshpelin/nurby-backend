"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Zone {
  id: string;
  camera_id: string;
  label: string;
  polygon: number[][];
  source: string;
  auto_score: number | null;
  active: boolean;
  locked: boolean;
  detected_at: string | null;
  last_seen_at: string | null;
  ptz_pose: { pan?: number; tilt?: number; zoom?: number } | null;
  stale_after_seconds: number;
}

interface Props {
  cameraId: string;
  targets: string[];
  setTargets: (v: string[]) => void;
  blurStrength: number;
  setBlurStrength: (v: number) => void;
}

const FALLBACK_TARGETS = [
  "bed",
  "tv",
  "monitor",
  "laptop",
  "computer monitor",
  "computer keyboard",
  "cell phone",
  "mobile phone",
  "window",
  "door",
  "toilet",
  "bathtub",
  "mirror",
  "picture frame",
];

/**
 * Camera-edit subsection. Shows detected zones with toggle / lock /
 * delete controls plus a chip picker for the target labels and a
 * blur-strength slider. Zone detection itself runs on the
 * perception worker as a side-effect of regular keyframe processing
 * so we don't need a manual "Detect now" button; new zones land
 * automatically once a target label appears in a frame.
 */
export function PrivacyZonesSection({
  cameraId,
  targets,
  setTargets,
  blurStrength,
  setBlurStrength,
}: Props) {
  const { authFetch } = useAuth();
  const [zones, setZones] = useState<Zone[]>([]);
  const [available, setAvailable] = useState<string[]>(FALLBACK_TARGETS);
  const [loading, setLoading] = useState(false);

  const fetchZones = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`/api/privacy-zones?camera_id=${cameraId}`);
      if (res.ok) setZones(await res.json());
    } finally {
      setLoading(false);
    }
  }, [authFetch, cameraId]);

  useEffect(() => {
    fetchZones();
    (async () => {
      try {
        const res = await authFetch("/api/privacy-zones/targets");
        if (res.ok) setAvailable(await res.json());
      } catch {
        /* ignore */
      }
    })();
  }, [fetchZones, authFetch]);

  const patch = async (id: string, body: Record<string, unknown>) => {
    const res = await authFetch(`/api/privacy-zones/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) fetchZones();
  };

  const remove = async (id: string) => {
    await authFetch(`/api/privacy-zones/${id}`, { method: "DELETE" });
    fetchZones();
  };

  const toggleTarget = (t: string) => {
    setTargets(
      targets.includes(t)
        ? targets.filter((x) => x !== t)
        : [...targets, t]
    );
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-medium text-muted-foreground block mb-2">
          What should this camera blur?
        </label>
        <div className="flex flex-wrap gap-1.5">
          {available.map((t) => {
            const on = targets.includes(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => toggleTarget(t)}
                className={`px-2 py-1 text-xs rounded-md border transition-colors ${
                  on
                    ? "border-amber-500 bg-amber-500/15 text-amber-300"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {t}
              </button>
            );
          })}
        </div>
        <p className="text-[11px] text-muted-foreground mt-2 leading-relaxed">
          Pick the kinds of objects you want hidden. The detector
          runs on every keyframe; new zones appear here automatically.
        </p>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground block mb-2">
          Blur strength
        </label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={11}
            max={151}
            step={2}
            value={blurStrength}
            onChange={(e) => setBlurStrength(Number(e.target.value))}
            className="flex-1 accent-amber-500"
          />
          <span className="font-mono text-xs text-muted-foreground w-12 text-right">
            {blurStrength}
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground mt-1">
          Higher = heavier Gaussian. 55 obscures faces on a monitor.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs font-medium text-muted-foreground">
            Detected zones {zones.length > 0 ? `(${zones.length})` : ""}
          </label>
          {loading && (
            <span className="text-[10px] text-muted-foreground">loading.</span>
          )}
        </div>
        {zones.length === 0 ? (
          <div className="rounded-md border border-dashed border-border bg-card/30 px-3 py-4 text-xs text-muted-foreground text-center">
            No zones yet. Toggle a target above; the next keyframe
            that contains it will populate this list.
          </div>
        ) : (
          <div className="space-y-1.5">
            {zones.map((z) => (
              <div
                key={z.id}
                className={`flex items-center gap-2 rounded-md border px-2.5 py-2 ${
                  z.active
                    ? "border-amber-500/40 bg-amber-500/5"
                    : "border-border bg-card/40 opacity-60"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    z.active ? "bg-amber-400" : "bg-muted-foreground/40"
                  }`}
                />
                <span className="text-xs font-medium">{z.label}</span>
                <span className="text-[10px] text-muted-foreground font-mono">
                  {z.source}
                  {z.auto_score != null
                    ? ` · ${(z.auto_score * 100).toFixed(0)}%`
                    : ""}
                </span>
                <span className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => patch(z.id, { active: !z.active })}
                    className="text-[10px] text-muted-foreground hover:text-foreground px-1.5 py-0.5 rounded hover:bg-muted/50"
                    title={z.active ? "Pause this zone" : "Resume this zone"}
                  >
                    {z.active ? "pause" : "resume"}
                  </button>
                  <button
                    type="button"
                    onClick={() => patch(z.id, { locked: !z.locked })}
                    className={`text-[10px] px-1.5 py-0.5 rounded hover:bg-muted/50 ${
                      z.locked ? "text-amber-300" : "text-muted-foreground hover:text-foreground"
                    }`}
                    title={
                      z.locked
                        ? "Unlock so the detector can refresh the polygon"
                        : "Lock the polygon so the detector won't move it"
                    }
                  >
                    {z.locked ? "locked" : "lock"}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(z.id)}
                    className="text-[10px] text-danger/80 hover:text-danger px-1.5 py-0.5 rounded hover:bg-danger/10"
                  >
                    delete
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
