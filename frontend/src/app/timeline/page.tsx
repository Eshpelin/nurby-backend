"use client";

import { useCallback, useEffect, useState } from "react";

// API calls use relative paths, proxied by Next.js rewrites in dev

interface Recording {
  id: string;
  camera_id: string;
  file_path: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  file_size_bytes: number | null;
  thumbnail_path: string | null;
}

interface Observation {
  id: string;
  camera_id: string;
  started_at: string;
  ended_at: string | null;
  object_detections: { objects: Detection[]; count: number } | null;
  vlm_description: string | null;
  vlm_provider: string | null;
  confidence: number | null;
  thumbnail_path: string | null;
}

interface Detection {
  label: string;
  confidence: number;
  bbox: number[];
}

interface Camera {
  id: string;
  name: string;
  location_label: string | null;
}

// Unified timeline entry
interface TimelineEntry {
  id: string;
  type: "recording" | "observation";
  camera_id: string;
  timestamp: string;
  data: Recording | Observation;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "0s";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function formatSize(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function summarizeDetections(obs: Observation): string {
  if (!obs.object_detections || obs.object_detections.count === 0) {
    return "Motion detected, no objects identified";
  }
  const counts: Record<string, number> = {};
  for (const d of obs.object_detections.objects) {
    counts[d.label] = (counts[d.label] || 0) + 1;
  }
  const parts = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => (count === 1 ? label : `${count} ${label}s`));
  return parts.join(", ");
}

type TimeRange = "today" | "7d" | "30d";
type EventFilter = "all" | "recordings" | "observations";

export default function TimelinePage() {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [cameras, setCameras] = useState<Record<string, Camera>>({});
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [activeEntry, setActiveEntry] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");
  const [eventFilter, setEventFilter] = useState<EventFilter>("all");
  const [loading, setLoading] = useState(true);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await fetch("/api/cameras");
      if (!res.ok) return;
      const data: Camera[] = await res.json();
      const map: Record<string, Camera> = {};
      for (const c of data) map[c.id] = c;
      setCameras(map);
    } catch {
      /* silently fail */
    }
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (selectedCamera) params.set("camera_id", selectedCamera);

      const [recRes, obsRes] = await Promise.all([
        fetch(`/api/recordings?${params}`),
        fetch(`/api/observations?${params}`),
      ]);

      const now = Date.now();
      const cutoffs: Record<TimeRange, number> = {
        today: 24 * 60 * 60 * 1000,
        "7d": 7 * 24 * 60 * 60 * 1000,
        "30d": 30 * 24 * 60 * 60 * 1000,
      };
      const cutoff = now - cutoffs[timeRange];

      if (recRes.ok) {
        const data: Recording[] = await recRes.json();
        setRecordings(
          data.filter((r) => new Date(r.started_at).getTime() >= cutoff)
        );
      }
      if (obsRes.ok) {
        const data: Observation[] = await obsRes.json();
        setObservations(
          data.filter((o) => new Date(o.started_at).getTime() >= cutoff)
        );
      }
    } catch {
      /* silently fail */
    } finally {
      setLoading(false);
    }
  }, [selectedCamera, timeRange]);

  useEffect(() => {
    fetchCameras();
  }, [fetchCameras]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Build unified timeline entries
  let entries: TimelineEntry[] = [];

  if (eventFilter !== "observations") {
    entries.push(
      ...recordings.map((r) => ({
        id: `rec-${r.id}`,
        type: "recording" as const,
        camera_id: r.camera_id,
        timestamp: r.started_at,
        data: r,
      }))
    );
  }

  if (eventFilter !== "recordings") {
    entries.push(
      ...observations.map((o) => ({
        id: `obs-${o.id}`,
        type: "observation" as const,
        camera_id: o.camera_id,
        timestamp: o.started_at,
        data: o,
      }))
    );
  }

  // Sort by timestamp descending
  entries.sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  // Group by date
  const grouped: Record<string, TimelineEntry[]> = {};
  for (const e of entries) {
    const dateKey = formatDate(e.timestamp);
    if (!grouped[dateKey]) grouped[dateKey] = [];
    grouped[dateKey].push(e);
  }

  const cameraList = Object.values(cameras);
  const totalCount = entries.length;

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Timeline</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {totalCount} event{totalCount !== 1 ? "s" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 p-1 rounded-md bg-card border border-border">
            {(["today", "7d", "30d"] as TimeRange[]).map((range) => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${
                  timeRange === range
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {range === "today" ? "Today" : range}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* Filter sidebar */}
        <aside className="col-span-3 space-y-5">
          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Camera
            </div>
            {cameraList.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No cameras configured
              </p>
            ) : (
              <div className="space-y-1">
                <button
                  onClick={() => setSelectedCamera(null)}
                  className={`block w-full text-left px-2 py-1.5 text-sm rounded transition-colors ${
                    !selectedCamera
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  All cameras
                </button>
                {cameraList.map((cam) => (
                  <button
                    key={cam.id}
                    onClick={() => setSelectedCamera(cam.id)}
                    className={`block w-full text-left px-2 py-1.5 text-sm rounded transition-colors ${
                      selectedCamera === cam.id
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {cam.name}
                    {cam.location_label && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        {cam.location_label}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Event type
            </div>
            <div className="space-y-1">
              {(
                [
                  ["all", "All events"],
                  ["recordings", "Recordings"],
                  ["observations", "AI observations"],
                ] as [EventFilter, string][]
              ).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setEventFilter(value)}
                  className={`block w-full text-left px-2 py-1.5 text-sm rounded transition-colors ${
                    eventFilter === value
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </aside>

        {/* Timeline feed */}
        <section className="col-span-9">
          {loading && entries.length === 0 ? (
            <div className="flex items-center justify-center py-20">
              <div className="text-sm text-muted-foreground">
                Loading timeline.
              </div>
            </div>
          ) : entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
                ?
              </div>
              <p className="text-muted-foreground text-sm">
                No events found in this time range. Events appear here once
                cameras are connected and services are running.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {Object.entries(grouped).map(([date, dateEntries]) => (
                <div key={date}>
                  <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
                    {date}
                  </div>
                  <div className="space-y-2">
                    {dateEntries.map((entry) => {
                      const cam = cameras[entry.camera_id];
                      const isActive = activeEntry === entry.id;

                      if (entry.type === "recording") {
                        const rec = entry.data as Recording;
                        return (
                          <div key={entry.id}>
                            <button
                              onClick={() =>
                                setActiveEntry(isActive ? null : entry.id)
                              }
                              className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                                isActive
                                  ? "border-accent bg-card"
                                  : "border-border hover:border-accent/50 hover:bg-card/50"
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                  <div className="w-2 h-2 rounded-full bg-blue-500" />
                                  <div>
                                    <div className="text-sm font-medium">
                                      {cam?.name || "Unknown Camera"}
                                      <span className="ml-2 text-xs text-muted-foreground font-normal">
                                        Recording
                                      </span>
                                    </div>
                                    <div className="font-mono text-xs text-muted-foreground mt-0.5">
                                      {formatTime(rec.started_at)}
                                      {rec.ended_at &&
                                        ` \u2192 ${formatTime(rec.ended_at)}`}
                                    </div>
                                  </div>
                                </div>
                                <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                                  <span>
                                    {formatDuration(rec.duration_seconds)}
                                  </span>
                                  <span>
                                    {formatSize(rec.file_size_bytes)}
                                  </span>
                                </div>
                              </div>
                            </button>

                            {isActive && (
                              <div className="mt-2 rounded-lg overflow-hidden border border-border bg-black">
                                <video
                                  controls
                                  autoPlay
                                  className="w-full aspect-video"
                                  src={`/api/recordings/${rec.id}/stream`}
                                >
                                  Your browser does not support video playback.
                                </video>
                              </div>
                            )}
                          </div>
                        );
                      }

                      // Observation entry
                      const obs = entry.data as Observation;
                      const detSummary = summarizeDetections(obs);

                      return (
                        <div key={entry.id}>
                          <button
                            onClick={() =>
                              setActiveEntry(isActive ? null : entry.id)
                            }
                            className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                              isActive
                                ? "border-accent bg-card"
                                : "border-border hover:border-accent/50 hover:bg-card/50"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-3">
                                <div className="w-2 h-2 rounded-full bg-green-500" />
                                <div>
                                  <div className="text-sm font-medium">
                                    {cam?.name || "Unknown Camera"}
                                    <span className="ml-2 text-xs text-green-400 font-normal">
                                      AI Observation
                                    </span>
                                  </div>
                                  <div className="text-xs text-muted-foreground mt-0.5">
                                    {detSummary}
                                  </div>
                                </div>
                              </div>
                              <div className="text-xs text-muted-foreground font-mono">
                                {formatTime(obs.started_at)}
                              </div>
                            </div>
                          </button>

                          {isActive && (
                            <div className="mt-2 rounded-lg border border-border bg-card p-4 space-y-3">
                              {obs.thumbnail_path && (
                                <div className="rounded-lg overflow-hidden border border-border">
                                  <img
                                    src={`/api/observations/${obs.id}/thumbnail`}
                                    alt="Detection thumbnail"
                                    className="w-full"
                                  />
                                </div>
                              )}

                              {obs.vlm_description && (
                                <div>
                                  <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
                                    AI Description
                                  </div>
                                  <p className="text-sm">
                                    {obs.vlm_description}
                                  </p>
                                  {obs.vlm_provider && (
                                    <p className="text-xs text-muted-foreground mt-1 font-mono">
                                      via {obs.vlm_provider}
                                    </p>
                                  )}
                                </div>
                              )}

                              {obs.object_detections &&
                                obs.object_detections.count > 0 && (
                                  <div>
                                    <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
                                      Detections
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                      {obs.object_detections.objects.map(
                                        (d, i) => (
                                          <span
                                            key={i}
                                            className="px-2 py-0.5 text-xs rounded-full bg-muted border border-border"
                                          >
                                            {d.label}{" "}
                                            <span className="text-muted-foreground">
                                              {(d.confidence * 100).toFixed(0)}%
                                            </span>
                                          </span>
                                        )
                                      )}
                                    </div>
                                  </div>
                                )}

                              <div className="text-xs text-muted-foreground font-mono">
                                {formatTime(obs.started_at)}
                                {obs.confidence &&
                                  ` | confidence ${(obs.confidence * 100).toFixed(0)}%`}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
