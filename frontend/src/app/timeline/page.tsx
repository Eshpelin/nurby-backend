"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

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

interface StatusLog {
  id: string;
  camera_id: string;
  status: string;
  previous_status: string | null;
  reason: string | null;
  timestamp: string;
}

interface Camera {
  id: string;
  name: string;
  location_label: string | null;
}

// Unified timeline entry
interface TimelineEntry {
  id: string;
  type: "recording" | "observation" | "status";
  camera_id: string;
  timestamp: string;
  data: Recording | Observation | StatusLog;
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

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    live: "Online",
    recording: "Recording",
    offline: "Offline",
    error: "Error",
  };
  return map[status] || status;
}

function statusColor(status: string): string {
  const map: Record<string, string> = {
    live: "bg-green-500",
    recording: "bg-danger",
    offline: "bg-gray-500",
    error: "bg-warning",
  };
  return map[status] || "bg-gray-500";
}

type TimeRange = "today" | "7d" | "30d";
type EventFilter = "all" | "recordings" | "observations" | "status";

function TimelineContent() {
  const searchParams = useSearchParams();
  const initialCamera = searchParams.get("camera");

  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [statusLogs, setStatusLogs] = useState<StatusLog[]>([]);
  const [cameras, setCameras] = useState<Record<string, Camera>>({});
  const [selectedCamera, setSelectedCamera] = useState<string | null>(initialCamera);
  const [activeEntry, setActiveEntry] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");
  const [eventFilter, setEventFilter] = useState<EventFilter>("all");
  const [loading, setLoading] = useState(true);
  const [digest, setDigest] = useState<{
    period_label: string;
    total_observations: number;
    summary: string;
    highlights: string[];
    stats: Record<string, unknown>;
  } | null>(null);
  const [digestPeriod, setDigestPeriod] = useState<"daily" | "hourly">("daily");
  const [digestLoading, setDigestLoading] = useState(false);
  const [liveEvents, setLiveEvents] = useState<{ type: string; rule_name?: string; camera_id?: string; timestamp?: string; message?: string }[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // WebSocket connection for real-time events
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 5000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          if (data.type === "event" || data.type === "notification") {
            setLiveEvents((prev) => [data, ...prev].slice(0, 20));
            // Trigger data refresh to pick up new observations
            fetchData();
          }
        } catch {
          /* ignore non-JSON */
        }
      };
    }

    connect();
    return () => {
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

      const statusParams = new URLSearchParams({ limit: "100" });
      if (selectedCamera) statusParams.set("camera_id", selectedCamera);

      const [recRes, obsRes, statusRes] = await Promise.all([
        fetch(`/api/recordings?${params}`),
        fetch(`/api/observations?${params}`),
        fetch(`/api/cameras/status-logs?${statusParams}`),
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
      if (statusRes.ok) {
        const data: StatusLog[] = await statusRes.json();
        setStatusLogs(
          data.filter((s) => new Date(s.timestamp).getTime() >= cutoff)
        );
      }
    } catch {
      /* silently fail */
    } finally {
      setLoading(false);
    }
  }, [selectedCamera, timeRange]);

  const fetchDigest = useCallback(async () => {
    setDigestLoading(true);
    try {
      const params = new URLSearchParams({ period: digestPeriod });
      if (selectedCamera) params.set("camera_id", selectedCamera);
      const res = await fetch(`/api/search/digest?${params}`);
      if (res.ok) setDigest(await res.json());
    } catch {
      /* silent */
    } finally {
      setDigestLoading(false);
    }
  }, [digestPeriod, selectedCamera]);

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

  if (eventFilter === "all" || eventFilter === "recordings") {
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

  if (eventFilter === "all" || eventFilter === "observations") {
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

  if (eventFilter === "all" || eventFilter === "status") {
    entries.push(
      ...statusLogs.map((s) => ({
        id: `status-${s.id}`,
        type: "status" as const,
        camera_id: s.camera_id,
        timestamp: s.timestamp,
        data: s,
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
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                wsConnected ? "bg-green-500 pulse-dot" : "bg-red-500"
              }`}
            />
            <span className="text-muted-foreground font-mono">
              {wsConnected ? "live" : "disconnected"}
            </span>
          </div>
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
                  ["status", "Status changes"],
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

          {/* Digest */}
          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Activity digest
            </div>
            <div className="flex gap-1 mb-2">
              {(["daily", "hourly"] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setDigestPeriod(p)}
                  className={`px-2 py-1 text-xs rounded transition-colors ${
                    digestPeriod === p
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {p === "daily" ? "24h" : "1h"}
                </button>
              ))}
              <button
                onClick={fetchDigest}
                disabled={digestLoading}
                className="px-2 py-1 text-xs rounded border border-border text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50 ml-auto"
              >
                {digestLoading ? "..." : "Generate"}
              </button>
            </div>
            {digest ? (
              <div className="rounded-md border border-border bg-card/50 p-3 space-y-2">
                <div className="text-xs text-muted-foreground font-mono">
                  {digest.period_label}
                </div>
                <p className="text-sm leading-relaxed">{digest.summary}</p>
                {digest.highlights.length > 0 && (
                  <div className="space-y-1">
                    {digest.highlights.map((h, i) => (
                      <div key={i} className="text-xs text-muted-foreground">
                        {h}
                      </div>
                    ))}
                  </div>
                )}
                <div className="text-[10px] text-muted-foreground font-mono">
                  {digest.total_observations} observations
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Click Generate to create a summary of recent activity
              </p>
            )}
          </div>
        </aside>

        {/* Timeline feed */}
        <section className="col-span-9">
          {/* Live event toasts */}
          {liveEvents.length > 0 && (
            <div className="mb-4 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-accent uppercase tracking-wider flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
                  Live events
                </span>
                <button
                  onClick={() => setLiveEvents([])}
                  className="text-[10px] text-muted-foreground hover:text-foreground"
                >
                  clear
                </button>
              </div>
              {liveEvents.slice(0, 5).map((evt, i) => (
                <div
                  key={i}
                  className="px-3 py-2 rounded-md border border-accent/30 bg-accent/5 text-sm flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      evt.type === "notification"
                        ? "bg-yellow-400"
                        : "bg-accent"
                    }`} />
                    <span>
                      {evt.message || `Rule "${evt.rule_name}" fired`}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground font-mono">
                    {evt.timestamp
                      ? formatTime(evt.timestamp)
                      : "now"}
                  </span>
                </div>
              ))}
            </div>
          )}
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

                      if (entry.type === "status") {
                        const log = entry.data as StatusLog;
                        const isOnline = log.status === "live" || log.status === "recording";
                        return (
                          <div
                            key={entry.id}
                            className="px-4 py-2.5 rounded-lg border border-border/50 flex items-center justify-between"
                          >
                            <div className="flex items-center gap-3">
                              <div className={`w-2 h-2 rounded-full ${statusColor(log.status)}`} />
                              <div>
                                <div className="text-sm">
                                  <span className="font-medium">{cam?.name || "Unknown Camera"}</span>
                                  <span className="mx-1.5 text-muted-foreground">went</span>
                                  <span className={isOnline ? "text-green-400" : "text-muted-foreground"}>
                                    {statusLabel(log.status).toLowerCase()}
                                  </span>
                                </div>
                                {log.reason && (
                                  <div className="text-xs text-muted-foreground mt-0.5">
                                    {log.reason}
                                  </div>
                                )}
                              </div>
                            </div>
                            <span className="text-xs text-muted-foreground font-mono">
                              {formatTime(log.timestamp)}
                            </span>
                          </div>
                        );
                      }

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

export default function TimelinePage() {
  return (
    <Suspense>
      <TimelineContent />
    </Suspense>
  );
}
