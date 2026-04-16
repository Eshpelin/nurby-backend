"use client";

import { Suspense, useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";

const WEBRTC_URL =
  process.env.NEXT_PUBLIC_WEBRTC_URL || "http://localhost:8889";

type StreamType = "rtsp" | "http_mjpeg" | "http_snapshot" | "hls" | "usb" | "file";

interface Camera {
  id: string;
  name: string;
  stream_url: string;
  stream_type: StreamType;
  location_label: string | null;
  status: "offline" | "live" | "recording";
  width: number | null;
  height: number | null;
  fps: number | null;
  recording_enabled: boolean;
  digest_enabled: boolean;
  digest_period: string;
  created_at: string;
  updated_at: string;
}

interface Person {
  id: string;
  display_name: string;
}

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

interface FaceDetection {
  person_name: string | null;
  person_id: string | null;
  match_distance?: number | null;
  bbox?: number[];
}

interface Observation {
  id: string;
  camera_id: string;
  started_at: string;
  ended_at: string | null;
  object_detections: { objects: Detection[]; count: number } | null;
  person_detections: { faces: FaceDetection[]; count: number } | null;
  vlm_description: string | null;
  vlm_provider: string | null;
  confidence: number | null;
  thumbnail_path: string | null;
}

interface Detection {
  label: string;
  confidence: number;
  bbox: number[];
  plate_text?: string | null;
}

interface StatusLog {
  id: string;
  camera_id: string;
  status: string;
  previous_status: string | null;
  reason: string | null;
  timestamp: string;
}

interface SearchResult {
  id: string;
  camera_id: string;
  camera_name: string;
  started_at: string;
  object_detections: { objects: { label: string; confidence: number; plate_text?: string | null }[]; count: number } | null;
  person_detections: { faces: FaceDetection[]; count: number } | null;
  vlm_description: string | null;
  confidence: number | null;
  thumbnail_path: string | null;
}

interface Digest {
  period: string;
  period_label: string;
  total_observations: number;
  summary: string;
  highlights: string[];
}

interface TimelineEntry {
  id: string;
  type: "recording" | "observation" | "status" | "search_result";
  camera_id: string;
  timestamp: string;
  data: Recording | Observation | StatusLog | SearchResult;
}

interface ActivityEvent {
  id: string;
  timestamp: string;
  summary: string;
  icon: "person" | "object" | "scene";
}

const OBJECT_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle",
  "dog", "cat", "bird", "backpack", "handbag",
  "suitcase", "umbrella", "cell phone", "laptop",
];

const STREAM_TYPES: { value: StreamType; label: string; hint: string; placeholder: string }[] = [
  { value: "rtsp", label: "RTSP", hint: "IP cameras, NVRs, most security cameras", placeholder: "rtsp://192.168.1.100:554/stream1" },
  { value: "http_mjpeg", label: "HTTP MJPEG", hint: "Motion JPEG over HTTP. Webcams, ESP32-CAM", placeholder: "http://192.168.1.100:8080/video" },
  { value: "http_snapshot", label: "HTTP Snapshot", hint: "Periodic JPEG pull. Low-bandwidth cameras", placeholder: "http://192.168.1.100/snapshot.jpg" },
  { value: "hls", label: "HLS", hint: "HTTP Live Streaming. Cloud cameras, Wyze, Ring", placeholder: "http://192.168.1.100/live/stream.m3u8" },
  { value: "usb", label: "USB / Local", hint: "Locally attached USB or CSI cameras", placeholder: "0" },
  { value: "file", label: "File / Test", hint: "Local video file for testing", placeholder: "/path/to/video.mp4" },
];

type TimeRange = "today" | "7d" | "30d";
type EventFilter = "all" | "recordings" | "observations" | "status";

// ── Helpers ──

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "0s";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m === 0 ? `${s}s` : `${m}m ${s}s`;
}

function formatSize(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extractStreamName(streamUrl: string): string {
  try {
    const path = streamUrl.replace(/\/+$/, "");
    const lastSlash = path.lastIndexOf("/");
    return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
  } catch {
    return streamUrl;
  }
}

function summarizeDetections(obs: Observation): string {
  const parts: string[] = [];

  // Person names first
  if (obs.person_detections?.faces) {
    const named = obs.person_detections.faces.filter((f) => f.person_name);
    const unnamed = obs.person_detections.faces.filter((f) => !f.person_name);
    for (const f of named) {
      parts.push(f.person_name!);
    }
    if (unnamed.length > 0) {
      parts.push(unnamed.length === 1 ? "unknown person" : `${unnamed.length} unknown people`);
    }
  }

  // License plates
  if (obs.object_detections?.objects) {
    for (const d of obs.object_detections.objects) {
      if (d.label === "license_plate" && d.plate_text) {
        parts.push(`plate ${d.plate_text}`);
      }
    }
  }

  // Object counts (skip person since we handled faces above, skip license_plate since handled)
  if (obs.object_detections?.objects && obs.object_detections.objects.length > 0) {
    const counts: Record<string, number> = {};
    for (const d of obs.object_detections.objects) {
      if (d.label === "person" || d.label === "license_plate") continue;
      counts[d.label] = (counts[d.label] || 0) + 1;
    }
    const objectParts = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => (count === 1 ? label : `${count} ${label}s`));
    parts.push(...objectParts);
  }

  if (parts.length === 0) {
    return obs.vlm_description
      ? obs.vlm_description.split(/\.\s/)[0].slice(0, 60)
      : "Motion detected";
  }

  return parts.join(", ") + " detected";
}

function observationToEvents(obs: Observation): ActivityEvent[] {
  const events: ActivityEvent[] = [];

  if (obs.person_detections?.faces) {
    const named = obs.person_detections.faces.filter((f) => f.person_name);
    const unnamed = obs.person_detections.faces.filter((f) => !f.person_name);

    for (const face of named) {
      events.push({
        id: `${obs.id}-person-${face.person_name}`,
        timestamp: obs.started_at,
        summary: `${face.person_name} spotted`,
        icon: "person",
      });
    }

    if (unnamed.length > 0 && named.length === 0) {
      events.push({
        id: `${obs.id}-unknown-persons`,
        timestamp: obs.started_at,
        summary: unnamed.length === 1 ? "Unknown person detected" : `${unnamed.length} unknown people detected`,
        icon: "person",
      });
    }
  }

  if (obs.object_detections?.objects && obs.object_detections.objects.length > 0) {
    const counts: Record<string, number> = {};
    for (const obj of obs.object_detections.objects) {
      counts[obj.label] = (counts[obj.label] || 0) + 1;
    }
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 2);
    const parts = sorted.map(([label, count]) => count === 1 ? label : `${count} ${label}s`);

    if (events.length === 0 && parts.length > 0) {
      events.push({
        id: `${obs.id}-objects`,
        timestamp: obs.started_at,
        summary: parts.join(", ") + " detected",
        icon: "object",
      });
    }
  }

  if (events.length === 0 && obs.vlm_description) {
    let desc = obs.vlm_description.split(/\.\s/)[0];
    if (desc.length > 60) desc = desc.slice(0, 57) + "...";
    events.push({
      id: `${obs.id}-scene`,
      timestamp: obs.started_at,
      summary: desc,
      icon: "scene",
    });
  }

  return events;
}

function statusColor(status: string): string {
  const map: Record<string, string> = { live: "bg-green-500", recording: "bg-danger", offline: "bg-gray-500", error: "bg-warning" };
  return map[status] || "bg-gray-500";
}

function statusLabel(status: string): string {
  const map: Record<string, string> = { live: "Online", recording: "Recording", offline: "Offline", error: "Error" };
  return map[status] || status;
}

// ── Detection Overlay ──

const DEFAULT_FRAME_WIDTH = 1920;
const DEFAULT_FRAME_HEIGHT = 1080;
const DETECTION_FADE_MS = 10000;
const DETECTION_POLL_MS = 5000;

interface OverlayDetection {
  label: string;
  bbox: number[];
  color: string;
  borderColor: string;
}

function DetectionOverlay({ cameraId, visible, frameWidth, frameHeight }: {
  cameraId: string;
  visible: boolean;
  frameWidth: number;
  frameHeight: number;
}) {
  const { authFetch } = useAuth();
  const [detections, setDetections] = useState<OverlayDetection[]>([]);
  const [lastUpdated, setLastUpdated] = useState(0);
  const [faded, setFaded] = useState(false);
  const lastObsIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!visible) return;

    let cancelled = false;

    async function poll() {
      try {
        const res = await authFetch(`/api/observations?camera_id=${cameraId}&limit=1`);
        if (!res.ok || cancelled) return;
        const obs: Observation[] = await res.json();
        if (cancelled || obs.length === 0) return;

        const latest = obs[0];
        if (latest.id === lastObsIdRef.current) return;
        lastObsIdRef.current = latest.id;

        const boxes: OverlayDetection[] = [];

        if (latest.object_detections?.objects) {
          for (const obj of latest.object_detections.objects) {
            if (obj.bbox && obj.bbox.length === 4) {
              boxes.push({
                label: `${obj.label} ${Math.round(obj.confidence * 100)}%`,
                bbox: obj.bbox,
                color: "rgba(34, 197, 94, 0.15)",
                borderColor: "rgb(34, 197, 94)",
              });
            }
          }
        }

        if (latest.person_detections?.faces) {
          for (const face of latest.person_detections.faces) {
            if (face.bbox && face.bbox.length === 4) {
              const isKnown = !!face.person_name;
              boxes.push({
                label: face.person_name || "Unknown",
                bbox: face.bbox,
                color: isKnown ? "rgba(59, 130, 246, 0.15)" : "rgba(234, 179, 8, 0.15)",
                borderColor: isKnown ? "rgb(59, 130, 246)" : "rgb(234, 179, 8)",
              });
            }
          }
        }

        if (boxes.length > 0) {
          setDetections(boxes);
          setLastUpdated(Date.now());
          setFaded(false);
        }
      } catch { /* silent */ }
    }

    poll();
    const interval = setInterval(poll, DETECTION_POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [cameraId, visible, authFetch]);

  useEffect(() => {
    if (lastUpdated === 0) return;
    const timer = setTimeout(() => setFaded(true), DETECTION_FADE_MS);
    return () => clearTimeout(timer);
  }, [lastUpdated]);

  if (!visible || detections.length === 0) return null;

  return (
    <div className={`absolute inset-0 z-[5] pointer-events-none transition-opacity duration-500 ${faded ? "opacity-0" : "opacity-100"}`}>
      {detections.map((det, i) => {
        const [x1, y1, x2, y2] = det.bbox;
        const left = (x1 / frameWidth) * 100;
        const top = (y1 / frameHeight) * 100;
        const width = ((x2 - x1) / frameWidth) * 100;
        const height = ((y2 - y1) / frameHeight) * 100;

        return (
          <div key={`${det.label}-${i}`} style={{
            position: "absolute",
            left: `${left}%`,
            top: `${top}%`,
            width: `${width}%`,
            height: `${height}%`,
            border: `2px solid ${det.borderColor}`,
            backgroundColor: det.color,
            borderRadius: "2px",
          }}>
            <span style={{
              position: "absolute",
              top: "-18px",
              left: "0",
              fontSize: "10px",
              lineHeight: "16px",
              padding: "0 4px",
              backgroundColor: det.borderColor,
              color: "#000",
              borderRadius: "2px",
              whiteSpace: "nowrap",
              fontWeight: 600,
            }}>
              {det.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ── Camera sidebar card (compact) ──

type CameraLayout = "single" | "double" | "list";

function CameraSidebarCard({
  camera,
  selected,
  onClick,
  activityEvents,
  layout,
}: {
  camera: Camera;
  selected: boolean;
  onClick: () => void;
  activityEvents: ActivityEvent[];
  layout: CameraLayout;
}) {
  const [overlayVisible, setOverlayVisible] = useState(true);
  const streamName = extractStreamName(camera.stream_url);
  const iframeSrc = `${WEBRTC_URL}/${streamName}/`;
  const latestEvent = activityEvents[0];
  const frameW = camera.width || DEFAULT_FRAME_WIDTH;
  const frameH = camera.height || DEFAULT_FRAME_HEIGHT;

  // Activity stats
  const now = Date.now();
  const events1h = activityEvents.filter((e) => now - new Date(e.timestamp).getTime() < 3600000);
  const events24h = activityEvents.filter((e) => now - new Date(e.timestamp).getTime() < 86400000);

  // List layout. Compact horizontal row
  if (layout === "list") {
    return (
      <div
        onClick={onClick}
        className={`rounded-md border overflow-hidden cursor-pointer transition-colors group flex items-center gap-2.5 px-2.5 py-2 ${
          selected ? "border-accent bg-card" : "border-border bg-card hover:border-muted-foreground/30"
        }`}
      >
        {/* Tiny preview */}
        <div className="relative w-16 h-10 bg-black rounded overflow-hidden flex-shrink-0">
          {camera.status !== "offline" ? (
            <iframe src={iframeSrc} className="absolute inset-0 w-full h-full border-0 pointer-events-none scale-[1.5] origin-center" allow="autoplay; encrypted-media" sandbox="allow-scripts allow-same-origin" />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center"><span className="text-[8px] text-muted-foreground font-mono">OFF</span></div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${camera.status === "recording" ? "bg-danger" : camera.status === "live" ? "bg-green-500" : "bg-gray-400"} ${camera.status !== "offline" ? "pulse-dot" : ""}`} />
            <span className="text-xs font-medium truncate">{camera.name}</span>
          </div>
          {latestEvent && (
            <div className="text-[10px] text-muted-foreground truncate mt-0.5">{latestEvent.summary} · {timeAgo(latestEvent.timestamp)}</div>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {events1h.length > 0 && <span className="text-[9px] font-mono text-accent bg-accent/10 px-1 py-0.5 rounded">{events1h.length} / 1h</span>}
          {events24h.length > 0 && <span className="text-[9px] font-mono text-muted-foreground bg-muted/50 px-1 py-0.5 rounded">{events24h.length} / 24h</span>}
        </div>
        <button onClick={(e) => { e.stopPropagation(); window.location.href = `/cameras/${camera.id}`; }}
          className="w-5 h-5 rounded flex items-center justify-center text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100 flex-shrink-0">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
      </div>
    );
  }

  // Card layout (single or double column)
  return (
    <div
      onClick={onClick}
      className={`rounded-lg border overflow-hidden cursor-pointer transition-colors group ${
        selected ? "border-accent bg-card" : "border-border bg-card hover:border-muted-foreground/30"
      }`}
    >
      {/* Feed preview */}
      <div className="relative aspect-video bg-black">
        {camera.status !== "offline" ? (
          <iframe
            src={iframeSrc}
            className="absolute inset-0 w-full h-full border-0 pointer-events-none"
            allow="autoplay; encrypted-media"
            sandbox="allow-scripts allow-same-origin"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[10px] text-muted-foreground font-mono">OFFLINE</span>
          </div>
        )}

        {/* Detection bounding box overlay */}
        {camera.status !== "offline" && (
          <DetectionOverlay cameraId={camera.id} visible={overlayVisible} frameWidth={frameW} frameHeight={frameH} />
        )}

        {/* Overlay toggle (eye icon) */}
        {camera.status !== "offline" && (
          <button
            onClick={(e) => { e.stopPropagation(); setOverlayVisible((v) => !v); }}
            className="absolute top-1.5 right-9 z-10 w-6 h-6 rounded-md bg-black/60 backdrop-blur-sm border border-white/10 flex items-center justify-center text-white/70 hover:text-white hover:bg-black/80 transition-colors opacity-0 group-hover:opacity-100"
            title={overlayVisible ? "Hide detections" : "Show detections"}
          >
            {overlayVisible ? (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
                <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            )}
          </button>
        )}

        {/* Settings gear */}
        <button
          onClick={(e) => { e.stopPropagation(); window.location.href = `/cameras/${camera.id}`; }}
          className="absolute top-1.5 right-1.5 z-10 w-6 h-6 rounded-md bg-black/60 backdrop-blur-sm border border-white/10 flex items-center justify-center text-white/70 hover:text-white hover:bg-black/80 transition-colors opacity-0 group-hover:opacity-100"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
        </button>

        {/* Activity counters overlay */}
        {(events1h.length > 0 || events24h.length > 0) && (
          <div className="absolute top-1.5 left-1.5 z-10 flex gap-1">
            {events1h.length > 0 && <span className="text-[9px] font-mono bg-accent/80 text-black px-1 py-0.5 rounded backdrop-blur-sm">{events1h.length} / 1h</span>}
            {events24h.length > 0 && events1h.length === 0 && <span className="text-[9px] font-mono bg-black/60 text-white/80 px-1 py-0.5 rounded backdrop-blur-sm">{events24h.length} / 24h</span>}
          </div>
        )}

        {/* Status + name overlay at bottom */}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-2.5 pb-2 pt-6">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white truncate">{camera.name}</span>
            <span className="inline-flex items-center gap-1 text-[10px] text-white/70">
              <span className={`w-1.5 h-1.5 rounded-full ${
                camera.status === "recording" ? "bg-danger" : camera.status === "live" ? "bg-green-500" : "bg-gray-400"
              } ${camera.status !== "offline" ? "pulse-dot" : ""}`} />
              {camera.status === "recording" ? "REC" : camera.status === "live" ? "LIVE" : "OFF"}
            </span>
          </div>
        </div>
      </div>

      {/* Latest activity line */}
      {latestEvent && (
        <div className="px-2.5 py-1.5 border-t border-border/50 flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
            latestEvent.icon === "person" ? "bg-green-500" : latestEvent.icon === "object" ? "bg-blue-400" : "bg-muted-foreground"
          }`} />
          <span className="text-[11px] text-muted-foreground truncate flex-1">{latestEvent.summary}</span>
          <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0">{timeAgo(latestEvent.timestamp)}</span>
        </div>
      )}
    </div>
  );
}

// ── Add Camera Modal ──

interface DiscoveredDevice {
  index: number;
  path: string;
  name: string;
  resolution: string;
}

interface DiscoveredOnvifDevice {
  ip: string;
  port: number;
  name: string;
  manufacturer: string;
  model: string;
  firmware: string | null;
  onvif_url: string;
  stream_url: string | null;
  profiles: string[];
  auth_required: boolean;
  resolution: string | null;
  already_added: boolean;
}

type ModalTab = "manual" | "scan";

function NetworkScanPanel({ onSelectDevice }: { onSelectDevice: (dev: DiscoveredOnvifDevice, username?: string, password?: string) => void }) {
  const { authFetch } = useAuth();
  const [devices, setDevices] = useState<DiscoveredOnvifDevice[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [authInputs, setAuthInputs] = useState<Record<string, { username: string; password: string }>>({});
  const [addingIp, setAddingIp] = useState<string | null>(null);

  async function handleScan() {
    setScanning(true);
    setScanError(null);
    setDevices([]);
    setHasScanned(false);
    try {
      const res = await authFetch("/api/cameras/discover?timeout=5");
      if (!res.ok) throw new Error("Network scan failed");
      const data: DiscoveredOnvifDevice[] = await res.json();
      setDevices(data);
      setHasScanned(true);
      if (data.length === 0) {
        setScanError("No ONVIF cameras found on the local network. Make sure the cameras are powered on and connected to the same network. Check that multicast traffic is not blocked by your firewall.");
      }
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed");
      setHasScanned(true);
    } finally {
      setScanning(false);
    }
  }

  function handleAuthChange(ip: string, field: "username" | "password", value: string) {
    setAuthInputs((prev) => ({ ...prev, [ip]: { ...prev[ip], [field]: value } }));
  }

  function handleAddDevice(dev: DiscoveredOnvifDevice) {
    setAddingIp(dev.ip);
    const auth = authInputs[dev.ip];
    onSelectDevice(dev, auth?.username, auth?.password);
  }

  const manufacturerIcon = (manufacturer: string) => {
    const m = manufacturer.toLowerCase();
    if (m.includes("hikvision")) return "HK";
    if (m.includes("dahua")) return "DH";
    if (m.includes("axis")) return "AX";
    if (m.includes("amcrest")) return "AM";
    if (m.includes("reolink")) return "RL";
    if (m.includes("uniview") || m.includes("unv")) return "UV";
    if (m.includes("vivotek")) return "VT";
    if (m.includes("hanwha") || m.includes("samsung")) return "HW";
    return manufacturer.slice(0, 2).toUpperCase();
  };

  return (
    <div className="space-y-4">
      <button type="button" onClick={handleScan} disabled={scanning}
        className="w-full px-3 py-3 text-sm rounded-md border border-dashed border-border hover:border-accent bg-muted/30 hover:bg-accent/5 transition-colors flex items-center justify-center gap-2.5 disabled:opacity-50 disabled:cursor-not-allowed">
        {scanning ? (
          <>
            <svg className="animate-spin h-4 w-4 text-accent-foreground" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-muted-foreground">Scanning network for ONVIF cameras...</span>
          </>
        ) : (
          <>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
              <path d="M2 12h20" />
            </svg>
            <span>{hasScanned ? "Rescan network" : "Scan Network"}</span>
          </>
        )}
      </button>

      {scanError && (
        <div className="rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">{scanError}</p>
        </div>
      )}

      {devices.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
              Found {devices.length} device{devices.length !== 1 ? "s" : ""}
            </span>
            <div className="flex-1 h-px bg-border" />
          </div>

          {devices.map((dev) => (
            <div key={dev.ip} className={`rounded-md border transition-colors ${dev.already_added ? "border-border bg-muted/10 opacity-60" : "border-border bg-muted/20 hover:border-muted-foreground"}`}>
              <div className="px-3 py-2.5 flex items-start gap-3">
                <div className="w-10 h-10 rounded-md bg-muted/50 border border-border flex items-center justify-center shrink-0">
                  <span className="text-[11px] font-bold text-muted-foreground">{manufacturerIcon(dev.manufacturer)}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{dev.name}</span>
                    {dev.already_added && (
                      <span className="shrink-0 text-[10px] font-medium text-muted-foreground bg-muted/50 px-1.5 py-0.5 rounded">Already added</span>
                    )}
                    {dev.auth_required && !dev.already_added && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-yellow-500">
                        <title>Authentication required</title>
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                      </svg>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <span className="font-mono text-[11px] text-muted-foreground">{dev.ip}</span>
                    {dev.resolution && <span className="font-mono text-[11px] text-muted-foreground px-1 py-0.5 rounded bg-muted/50">{dev.resolution}</span>}
                    {dev.profiles.length > 0 && <span className="text-[11px] text-muted-foreground">{dev.profiles.join(", ")}</span>}
                  </div>
                  {dev.firmware && <div className="text-[10px] text-muted-foreground mt-0.5 font-mono">FW {dev.firmware}</div>}
                </div>
                {!dev.already_added && (
                  <button type="button" onClick={() => handleAddDevice(dev)} disabled={addingIp === dev.ip}
                    className="shrink-0 px-2.5 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity disabled:opacity-50">
                    {addingIp === dev.ip ? "Adding..." : "Add"}
                  </button>
                )}
              </div>
              {dev.auth_required && !dev.already_added && (
                <div className="px-3 pb-2.5 pt-0">
                  <div className="grid grid-cols-2 gap-2">
                    <input type="text" placeholder="Username" value={authInputs[dev.ip]?.username || ""}
                      onChange={(e) => handleAuthChange(dev.ip, "username", e.target.value)}
                      className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent" />
                    <input type="password" placeholder="Password" value={authInputs[dev.ip]?.password || ""}
                      onChange={(e) => handleAuthChange(dev.ip, "password", e.target.value)}
                      className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent" />
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AddCameraModal({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const { authFetch } = useAuth();
  const [activeTab, setActiveTab] = useState<ModalTab>("manual");
  const [name, setName] = useState("");
  const [streamType, setStreamType] = useState<StreamType>("rtsp");
  const [streamUrl, setStreamUrl] = useState("");
  const [locationLabel, setLocationLabel] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [snapshotInterval, setSnapshotInterval] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [devices, setDevices] = useState<DiscoveredDevice[]>([]);
  const [scanningDevices, setScanningDevices] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [manualInput, setManualInput] = useState(false);
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState<number | null>(null);

  const selectedType = STREAM_TYPES.find((t) => t.value === streamType)!;
  const supportsAuth = ["rtsp", "http_mjpeg", "http_snapshot", "hls"].includes(streamType);
  const supportsSnapshotInterval = streamType === "http_snapshot";

  async function handleDetectDevices() {
    setScanningDevices(true);
    setScanError(null);
    setDevices([]);
    setSelectedDeviceIndex(null);
    try {
      const res = await authFetch("/api/cameras/devices");
      if (!res.ok) throw new Error("Failed to scan for devices");
      const data: DiscoveredDevice[] = await res.json();
      setDevices(data);
      if (data.length === 0) setScanError("No video devices found. Try manual input instead.");
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanningDevices(false);
    }
  }

  function handleSelectDevice(device: DiscoveredDevice) {
    setSelectedDeviceIndex(device.index);
    setStreamUrl(String(device.index));
    if (!name.trim()) setName(device.name);
  }

  async function handleSubmitCamera(payload: Record<string, unknown>) {
    setSubmitting(true);
    setError(null);
    try {
      const res = await authFetch(`/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Request failed with status ${res.status}`);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add camera");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleManualSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !streamUrl.trim()) return;
    const payload: Record<string, unknown> = {
      name: name.trim(),
      stream_url: streamUrl.trim(),
      stream_type: streamType,
      location_label: locationLabel.trim() || null,
    };
    if (supportsAuth && username.trim()) {
      payload.username = username.trim();
      if (password) payload.password = password;
    }
    if (supportsAuth && authToken.trim()) payload.auth_token = authToken.trim();
    if (supportsSnapshotInterval) payload.snapshot_interval = snapshotInterval;
    await handleSubmitCamera(payload);
  }

  function handleOnvifDeviceSelect(dev: DiscoveredOnvifDevice, devUsername?: string, devPassword?: string) {
    const payload: Record<string, unknown> = {
      name: dev.name || `${dev.manufacturer} ${dev.model}`,
      stream_url: dev.stream_url || `rtsp://${dev.ip}:554/stream1`,
      stream_type: "rtsp",
    };
    if (devUsername?.trim()) payload.username = devUsername.trim();
    if (devPassword?.trim()) payload.password = devPassword.trim();
    handleSubmitCamera(payload);
  }

  const inputClass = "w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg mx-4 rounded-lg border border-border bg-card-elevated p-6 shadow-xl max-h-[90vh] overflow-y-auto scrollbar-thin">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">Add Camera</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground transition-colors text-xl leading-none">&times;</button>
        </div>

        {/* Tab switcher */}
        <div className="flex gap-1 mb-5 p-1 rounded-md bg-muted/30 border border-border">
          <button type="button" onClick={() => setActiveTab("manual")}
            className={`flex-1 px-3 py-1.5 text-sm rounded transition-colors ${activeTab === "manual" ? "bg-card-elevated text-foreground font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            Manual Setup
          </button>
          <button type="button" onClick={() => setActiveTab("scan")}
            className={`flex-1 px-3 py-1.5 text-sm rounded transition-colors flex items-center justify-center gap-1.5 ${activeTab === "scan" ? "bg-card-elevated text-foreground font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /><path d="M2 12h20" />
            </svg>
            Scan Network
          </button>
        </div>

        {/* Scan Network tab */}
        {activeTab === "scan" && (
          <div>
            <NetworkScanPanel onSelectDevice={handleOnvifDeviceSelect} />
            {error && <p className="text-sm text-danger mt-3">{error}</p>}
          </div>
        )}

        {/* Manual Setup tab */}
        {activeTab === "manual" && (
          <form onSubmit={handleManualSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Name</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Front Door" required className={inputClass} />
            </div>

            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Feed Type</label>
              <div className="grid grid-cols-3 gap-1.5">
                {STREAM_TYPES.map((t) => (
                  <button key={t.value} type="button" onClick={() => { setStreamType(t.value); setStreamUrl(""); setDevices([]); setScanError(null); setSelectedDeviceIndex(null); setManualInput(false); }}
                    className={`px-2 py-2 text-xs rounded-md border transition-colors text-center ${streamType === t.value ? "border-accent bg-accent/10 text-accent-foreground" : "border-border hover:border-muted-foreground text-muted-foreground"}`}>
                    <div className="font-medium">{t.label}</div>
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-muted-foreground mt-1.5">{selectedType.hint}</p>
            </div>

            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">
                {streamType === "usb" ? "Device Index or Path" : streamType === "file" ? "File Path" : "Stream URL"}
              </label>
              {streamType === "usb" && !manualInput ? (
                <div className="space-y-3">
                  <button type="button" onClick={handleDetectDevices} disabled={scanningDevices}
                    className="w-full px-3 py-2.5 text-sm rounded-md border border-dashed border-border hover:border-accent bg-muted/30 hover:bg-accent/5 transition-colors flex items-center justify-center gap-2 disabled:opacity-50">
                    {scanningDevices ? (
                      <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/></svg><span className="text-muted-foreground">Scanning...</span></>
                    ) : (
                      <span>{devices.length > 0 ? "Rescan devices" : "Detect devices"}</span>
                    )}
                  </button>
                  {scanError && <p className="text-[11px] text-danger">{scanError}</p>}
                  {devices.map((device) => (
                    <button key={device.index} type="button" onClick={() => handleSelectDevice(device)}
                      className={`w-full text-left px-3 py-2.5 rounded-md border transition-colors ${selectedDeviceIndex === device.index ? "border-accent bg-accent/10" : "border-border hover:border-muted-foreground bg-muted/20"}`}>
                      <div className="flex items-center justify-between">
                        <div><div className="text-sm font-medium">{device.name}</div><div className="text-[11px] text-muted-foreground font-mono">{device.path !== String(device.index) ? device.path : `index ${device.index}`}</div></div>
                        <span className="font-mono text-[11px] text-muted-foreground">{device.resolution}</span>
                      </div>
                    </button>
                  ))}
                  <button type="button" onClick={() => setManualInput(true)} className="text-[11px] text-muted-foreground hover:text-foreground">Manual input instead</button>
                  <input type="hidden" value={streamUrl} required />
                </div>
              ) : (
                <div>
                  <input type="text" value={streamUrl} onChange={(e) => setStreamUrl(e.target.value)} placeholder={selectedType.placeholder} required className={`${inputClass} font-mono text-xs`} />
                  {streamType === "usb" && (
                    <div className="flex items-center justify-between mt-1">
                      <p className="text-[11px] text-muted-foreground">Use 0 for first USB camera, 1 for second</p>
                      <button type="button" onClick={() => setManualInput(false)} className="text-[11px] text-muted-foreground hover:text-foreground shrink-0 ml-2">Detect devices</button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {supportsSnapshotInterval && (
              <div>
                <label className="block text-sm text-muted-foreground mb-1.5">Poll Interval</label>
                <div className="flex items-center gap-3">
                  <input type="range" min={0.5} max={30} step={0.5} value={snapshotInterval} onChange={(e) => setSnapshotInterval(Number(e.target.value))} className="flex-1 accent-accent" />
                  <span className="font-mono text-xs text-muted-foreground w-12 text-right">{snapshotInterval}s</span>
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Location Label</label>
              <input type="text" value={locationLabel} onChange={(e) => setLocationLabel(e.target.value)} placeholder="Optional" className={inputClass} />
            </div>

            {supportsAuth && (
              <div>
                <button type="button" onClick={() => setShowAuth(!showAuth)} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                  <span className={`text-xs transition-transform ${showAuth ? "rotate-90" : ""}`}>▶</span>
                  Authentication <span className="text-[11px]">(optional)</span>
                </button>
                {showAuth && (
                  <div className="mt-3 space-y-3 pl-4 border-l border-border-subtle">
                    <div className="grid grid-cols-2 gap-3">
                      <div><label className="block text-[11px] text-muted-foreground mb-1">Username</label><input type="text" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" className={`${inputClass} text-xs`} /></div>
                      <div><label className="block text-[11px] text-muted-foreground mb-1">Password</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" className={`${inputClass} text-xs`} /></div>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground"><span className="flex-1 h-px bg-border" />or<span className="flex-1 h-px bg-border" /></div>
                    <div><label className="block text-[11px] text-muted-foreground mb-1">Bearer Token / API Key</label><input type="password" value={authToken} onChange={(e) => setAuthToken(e.target.value)} placeholder="Token for API-based cameras" className={`${inputClass} text-xs font-mono`} /></div>
                  </div>
                )}
              </div>
            )}

            {error && <p className="text-sm text-danger">{error}</p>}

            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">Cancel</button>
              <button type="submit" disabled={submitting || !name.trim() || !streamUrl.trim()} className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
                {submitting ? "Adding..." : "Add Camera"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ── Main unified page ──

const SEARCH_HINTS = [
  "when did the cat come in",
  "person at front door",
  "show me all vehicles today",
  "who was in the backyard this morning",
  "any packages delivered",
  "was the garage door left open",
  "dog in the yard",
  "kids playing outside",
  "delivery truck in driveway",
  "someone at the gate after dark",
  "bicycle on the sidewalk",
  "how many cars passed today",
  "motion near the fence",
  "Sarah Chen arriving home",
  "any animals in the garden",
  "mail carrier",
  "lights left on in kitchen",
  "unknown person at side door",
  "show me nighttime activity",
  "cars parked in driveway",
  "when was the last delivery",
  "people walking by the house",
  "suspicious activity last night",
  "kids getting off school bus",
  "raccoon in the trash",
  "sprinkler running",
  "someone left the gate open",
  "FedEx or UPS truck",
  "how many people visited today",
  "birds on the porch",
];

function DashboardContent() {
  const { authFetch } = useAuth();
  const searchParams = useSearchParams();
  const initialCamera = searchParams.get("camera");
  const [searchHint, setSearchHint] = useState(() => SEARCH_HINTS[Math.floor(Math.random() * SEARCH_HINTS.length)]);

  // Camera state
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [camerasLoading, setCamerasLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [activityEvents, setActivityEvents] = useState<Record<string, ActivityEvent[]>>({});
  const [selectedCamera, setSelectedCamera] = useState<string | null>(initialCamera);
  const [cameraLayout, setCameraLayout] = useState<CameraLayout>(() => {
    if (typeof window !== "undefined") {
      return (localStorage.getItem("nurby-camera-layout") as CameraLayout) || "single";
    }
    return "single";
  });

  // Timeline state
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [statusLogs, setStatusLogs] = useState<StatusLog[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [activeEntry, setActiveEntry] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");
  const [eventFilter, setEventFilter] = useState<EventFilter>("all");
  const [timelineLoading, setTimelineLoading] = useState(true);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchActive, setSearchActive] = useState(false);
  const [filterPerson, setFilterPerson] = useState("");
  const [filterObject, setFilterObject] = useState("");
  const [showSearchFilters, setShowSearchFilters] = useState(false);
  const [aiAnswer, setAiAnswer] = useState<string | null>(null);
  const [askingAi, setAskingAi] = useState(false);
  const [hasAiProvider, setHasAiProvider] = useState<boolean | null>(null);

  // Live events
  const [liveEvents, setLiveEvents] = useState<{ type: string; rule_name?: string; camera_id?: string; timestamp?: string; message?: string }[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Digest
  const [digest, setDigest] = useState<Digest | null>(null);
  const [digestPeriod, setDigestPeriod] = useState<"daily" | "hourly">("daily");
  const [digestLoading, setDigestLoading] = useState(false);

  // WebSocket
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => { setWsConnected(false); reconnectTimer = setTimeout(connect, 5000); };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          if (data.type === "event" || data.type === "notification") {
            setLiveEvents((prev) => [data, ...prev].slice(0, 20));
            fetchTimeline();
          }
        } catch { /* ignore */ }
      };
    }

    connect();
    return () => { clearTimeout(reconnectTimer); wsRef.current?.close(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch cameras
  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) setCameras(await res.json());
    } catch { /* silent */ }
    finally { setCamerasLoading(false); }
  }, []);

  const fetchActivity = useCallback(async (cameraId: string) => {
    try {
      const res = await authFetch(`/api/observations?camera_id=${cameraId}&limit=15`);
      if (res.ok) {
        const obs: Observation[] = await res.json();
        const events = obs.flatMap(observationToEvents).slice(0, 10);
        setActivityEvents((prev) => ({ ...prev, [cameraId]: events }));
      }
    } catch { /* silent */ }
  }, [authFetch]);

  const fetchPersons = useCallback(async () => {
    try {
      const res = await authFetch("/api/persons");
      if (res.ok) setPersons(await res.json());
    } catch { /* silent */ }
  }, []);

  // Fetch timeline data
  const fetchTimeline = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (selectedCamera) params.set("camera_id", selectedCamera);
      const statusParams = new URLSearchParams({ limit: "100" });
      if (selectedCamera) statusParams.set("camera_id", selectedCamera);

      const [recRes, obsRes, statusRes] = await Promise.all([
        authFetch(`/api/recordings?${params}`),
        authFetch(`/api/observations?${params}`),
        authFetch(`/api/cameras/status-logs?${statusParams}`),
      ]);

      const now = Date.now();
      const cutoffs: Record<TimeRange, number> = { today: 86400000, "7d": 604800000, "30d": 2592000000 };
      const cutoff = now - cutoffs[timeRange];

      if (recRes.ok) setRecordings((await recRes.json()).filter((r: Recording) => new Date(r.started_at).getTime() >= cutoff));
      if (obsRes.ok) setObservations((await obsRes.json()).filter((o: Observation) => new Date(o.started_at).getTime() >= cutoff));
      if (statusRes.ok) setStatusLogs((await statusRes.json()).filter((s: StatusLog) => new Date(s.timestamp).getTime() >= cutoff));
    } catch { /* silent */ }
    finally { setTimelineLoading(false); }
  }, [selectedCamera, timeRange, authFetch]);

  const fetchDigest = useCallback(async () => {
    setDigestLoading(true);
    try {
      const params = new URLSearchParams({ period: digestPeriod });
      if (selectedCamera) params.set("camera_id", selectedCamera);
      const res = await authFetch(`/api/search/digest?${params}`);
      if (res.ok) setDigest(await res.json());
    } catch { /* silent */ }
    finally { setDigestLoading(false); }
  }, [digestPeriod, selectedCamera]);

  // Search
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim() && !filterPerson && !filterObject) {
      setSearchActive(false); setSearchResults([]); setAiAnswer(null); return;
    }
    setIsSearching(true); setSearchActive(true); setAiAnswer(null);
    const params = new URLSearchParams();
    if (searchQuery.trim()) params.set("q", searchQuery.trim());
    if (selectedCamera) params.set("camera_id", selectedCamera);
    if (filterPerson) params.set("person", filterPerson);
    if (filterObject) params.set("object", filterObject);
    try {
      const res = await authFetch(`/api/search?${params}`);
      if (res.ok) setSearchResults((await res.json()).results);
    } catch { /* silent */ }
    finally { setIsSearching(false); }
  }, [searchQuery, selectedCamera, filterPerson, filterObject]);

  const handleAskAi = async () => {
    if (!searchQuery.trim()) return;
    setAskingAi(true);
    try {
      const res = await authFetch("/api/search/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: searchQuery.trim() }) });
      if (res.ok) {
        const data = await res.json();
        setAiAnswer(data.answer);
        if (data.sources?.length > 0 && searchResults.length === 0) { setSearchResults(data.sources); setSearchActive(true); }
      }
    } catch { /* silent */ }
    finally { setAskingAi(false); }
  };

  const clearSearch = () => { setSearchQuery(""); setSearchActive(false); setSearchResults([]); setAiAnswer(null); setFilterPerson(""); setFilterObject(""); };

  // Effects
  useEffect(() => {
    const i = setInterval(() => {
      setSearchHint(SEARCH_HINTS[Math.floor(Math.random() * SEARCH_HINTS.length)]);
    }, 5000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => {
    authFetch("/api/providers").then(r => r.ok ? r.json() : []).then((providers: { active: boolean }[]) => {
      setHasAiProvider(providers.some(p => p.active));
    }).catch(() => setHasAiProvider(false));
  }, [authFetch]);
  useEffect(() => { fetchCameras(); fetchPersons(); }, [fetchCameras, fetchPersons]);
  useEffect(() => { const i = setInterval(fetchCameras, 10000); return () => clearInterval(i); }, [fetchCameras]);
  useEffect(() => { fetchTimeline(); const i = setInterval(fetchTimeline, 15000); return () => clearInterval(i); }, [fetchTimeline]);
  useEffect(() => { if (cameras.length > 0) cameras.forEach((cam) => { if (!activityEvents[cam.id]) fetchActivity(cam.id); }); }, [cameras]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (cameras.length === 0) return; const i = setInterval(() => cameras.forEach((c) => fetchActivity(c.id)), 15000); return () => clearInterval(i); }, [cameras, fetchActivity]);

  // Build timeline entries
  let entries: TimelineEntry[] = [];
  const cameraMap: Record<string, Camera> = {};
  for (const c of cameras) cameraMap[c.id] = c;

  if (searchActive) {
    entries = searchResults.map((r) => ({ id: `search-${r.id}`, type: "search_result" as const, camera_id: r.camera_id, timestamp: r.started_at, data: r }));
  } else {
    if (eventFilter === "all" || eventFilter === "recordings") entries.push(...recordings.map((r) => ({ id: `rec-${r.id}`, type: "recording" as const, camera_id: r.camera_id, timestamp: r.started_at, data: r })));
    if (eventFilter === "all" || eventFilter === "observations") entries.push(...observations.map((o) => ({ id: `obs-${o.id}`, type: "observation" as const, camera_id: o.camera_id, timestamp: o.started_at, data: o })));
    if (eventFilter === "all" || eventFilter === "status") entries.push(...statusLogs.map((s) => ({ id: `status-${s.id}`, type: "status" as const, camera_id: s.camera_id, timestamp: s.timestamp, data: s })));
  }

  entries.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  const grouped: Record<string, TimelineEntry[]> = {};
  for (const e of entries) { const k = formatDate(e.timestamp); if (!grouped[k]) grouped[k] = []; grouped[k].push(e); }

  const totalCount = entries.length;
  const activeFilterCount = [filterPerson, filterObject].filter(Boolean).length;

  return (
    <div className="px-4 py-4 h-[calc(100vh-3.5rem)] flex flex-col">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              {cameras.length} camera{cameras.length !== 1 ? "s" : ""}
              {" · "}
              {searchActive ? `${totalCount} results` : `${totalCount} events`}
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-green-500 pulse-dot" : "bg-red-500"}`} />
            <span className="text-muted-foreground font-mono">{wsConnected ? "live" : "disconnected"}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {!searchActive && (
            <div className="flex items-center gap-1 p-0.5 rounded-md bg-card border border-border">
              {(["today", "7d", "30d"] as TimeRange[]).map((range) => (
                <button key={range} onClick={() => setTimeRange(range)}
                  className={`px-2 py-1 text-xs rounded transition-colors ${timeRange === range ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                  {range === "today" ? "Today" : range}
                </button>
              ))}
            </div>
          )}
          <button onClick={() => setModalOpen(true)} className="px-3 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90">
            + Add camera
          </button>
        </div>
      </div>

      <div className="flex gap-4 flex-1 min-h-0">
        {/* LEFT. Camera feeds */}
        <aside className={`flex-shrink-0 flex flex-col min-h-0 transition-all ${
          cameraLayout === "double" ? "w-[480px]" : cameraLayout === "list" ? "w-80" : "w-72"
        }`}>
          {/* Camera list header with layout toggle */}
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Cameras</span>
            <div className="flex items-center gap-0.5 p-0.5 rounded bg-muted/50 border border-border">
              {/* Single column */}
              <button onClick={() => { setCameraLayout("single"); localStorage.setItem("nurby-camera-layout", "single"); }}
                className={`p-1 rounded transition-colors ${cameraLayout === "single" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                title="Single column">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="10" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/><rect x="1" y="7" width="10" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/></svg>
              </button>
              {/* Double column */}
              <button onClick={() => { setCameraLayout("double"); localStorage.setItem("nurby-camera-layout", "double"); }}
                className={`p-1 rounded transition-colors ${cameraLayout === "double" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                title="Two columns">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><rect x="1" y="1" width="4" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/><rect x="7" y="1" width="4" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/><rect x="1" y="7" width="4" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/><rect x="7" y="7" width="4" height="4" rx="1" stroke="currentColor" strokeWidth="1.2"/></svg>
              </button>
              {/* List */}
              <button onClick={() => { setCameraLayout("list"); localStorage.setItem("nurby-camera-layout", "list"); }}
                className={`p-1 rounded transition-colors ${cameraLayout === "list" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                title="List view">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><rect x="1" y="1.5" width="10" height="2.5" rx="0.5" stroke="currentColor" strokeWidth="1.2"/><rect x="1" y="5" width="10" height="2.5" rx="0.5" stroke="currentColor" strokeWidth="1.2"/><rect x="1" y="8.5" width="10" height="2.5" rx="0.5" stroke="currentColor" strokeWidth="1.2"/></svg>
              </button>
            </div>
          </div>

          {/* All cameras button */}
          <button onClick={() => setSelectedCamera(null)}
            className={`w-full text-left px-2.5 py-1.5 text-xs rounded-md mb-2 transition-colors flex-shrink-0 ${
              !selectedCamera ? "bg-muted text-foreground font-medium" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}>
            All cameras
          </button>

          {/* Scrollable camera list */}
          <div className={`flex-1 overflow-y-auto scrollbar-thin pr-1 ${
            cameraLayout === "double" ? "grid grid-cols-2 gap-2 auto-rows-min content-start" : "space-y-2"
          }`}>
            {cameras.map((cam) => (
              <CameraSidebarCard
                key={cam.id}
                camera={cam}
                selected={selectedCamera === cam.id}
                onClick={() => setSelectedCamera(selectedCamera === cam.id ? null : cam.id)}
                activityEvents={activityEvents[cam.id] || []}
                layout={cameraLayout}
              />
            ))}

            {cameras.length === 0 && !camerasLoading && (
              <div onClick={() => setModalOpen(true)}
                className={`rounded-lg border border-dashed border-border hover:border-accent cursor-pointer flex items-center justify-center py-12 transition-colors ${cameraLayout === "double" ? "col-span-2" : ""}`}>
                <div className="text-center">
                  <div className="w-8 h-8 rounded-full border border-border flex items-center justify-center mx-auto mb-2 text-muted-foreground text-sm">+</div>
                  <div className="text-xs text-muted-foreground">Add camera</div>
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* RIGHT. Timeline + Search */}
        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Search bar */}
          <div className="flex-shrink-0 mb-3">
            <div className="relative">
              <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSearch(); } if (e.key === "Escape") clearSearch(); }}
                placeholder={`Try "${searchHint}"`}
                className="w-full bg-card border border-border focus:border-accent rounded-lg pl-9 pr-32 py-2.5 text-sm focus:outline-none transition-colors"
              />
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
              </svg>
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                {searchActive && <button onClick={clearSearch} className="px-1.5 py-0.5 text-[10px] rounded border border-border text-muted-foreground hover:bg-muted">Clear</button>}
                <button onClick={() => setShowSearchFilters(!showSearchFilters)}
                  className={`px-1.5 py-0.5 text-[10px] rounded border transition-colors ${showSearchFilters || activeFilterCount > 0 ? "border-accent text-accent" : "border-border text-muted-foreground hover:bg-muted"}`}>
                  Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}
                </button>
                {!isSearching && searchQuery.trim() && !searchActive && (
                  <button onClick={handleSearch} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground hover:bg-border">search</button>
                )}
              </div>
            </div>

            {showSearchFilters && (
              <div className="mt-2 rounded-lg border border-border bg-card p-2.5 flex gap-2">
                <div className="flex-1">
                  <label className="text-[10px] text-muted-foreground block mb-0.5">Person</label>
                  <select value={filterPerson} onChange={(e) => setFilterPerson(e.target.value)} className="w-full px-2 py-1 rounded-md bg-background border border-border text-xs">
                    <option value="">Any</option>
                    {persons.map((p) => <option key={p.id} value={p.display_name}>{p.display_name}</option>)}
                  </select>
                </div>
                <div className="flex-1">
                  <label className="text-[10px] text-muted-foreground block mb-0.5">Object</label>
                  <select value={filterObject} onChange={(e) => setFilterObject(e.target.value)} className="w-full px-2 py-1 rounded-md bg-background border border-border text-xs">
                    <option value="">Any</option>
                    {OBJECT_LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </div>
              </div>
            )}

            {searchActive && !aiAnswer && askingAi && (
              <div className="mt-2 rounded-lg border border-accent/40 bg-accent/5 p-4">
                <div className="flex items-center gap-3">
                  <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin flex-shrink-0" />
                  <div>
                    <p className="text-xs font-medium text-accent">Analyzing {searchResults.length} observation{searchResults.length !== 1 ? "s" : ""}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">AI is reading through camera data to answer your question.</p>
                  </div>
                </div>
              </div>
            )}

            {searchActive && !aiAnswer && !askingAi && (
              <div className="mt-2 rounded-lg border border-border bg-card p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className={hasAiProvider ? "text-accent" : "text-muted-foreground"}>
                      <path d="M12 2a4 4 0 0 1 4 4v1a2 2 0 0 1 2 2v1a4 4 0 0 1-2 3.46V16a6 6 0 0 1-12 0v-2.54A4 4 0 0 1 2 10V9a2 2 0 0 1 2-2V6a4 4 0 0 1 4-4" />
                      <circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" />
                    </svg>
                    <div>
                      <p className="text-xs font-medium">
                        {hasAiProvider ? "Want a smarter answer?" : "AI answers unavailable"}
                      </p>
                      <p className="text-[10px] text-muted-foreground">
                        {hasAiProvider
                          ? `Found ${searchResults.length} result${searchResults.length !== 1 ? "s" : ""}. AI can analyze these and give you a direct answer.`
                          : "Connect an AI provider in Settings to enable natural language answers."}
                      </p>
                    </div>
                  </div>
                  {hasAiProvider ? (
                    <button
                      onClick={handleAskAi}
                      className="px-3 py-1.5 text-xs rounded-md bg-accent text-black font-medium hover:opacity-90 flex items-center gap-1.5 whitespace-nowrap"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m5 12 5 5L20 7" /></svg>
                      Ask AI
                    </button>
                  ) : (
                    <a href="/settings" className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:border-accent/50 transition-colors whitespace-nowrap">
                      Go to Settings
                    </a>
                  )}
                </div>
              </div>
            )}

            {aiAnswer && (
              <div className="mt-2 rounded-lg border border-accent/40 bg-accent/5 p-3">
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
                    <span className="text-[10px] font-medium text-accent uppercase tracking-wider">AI Answer</span>
                  </div>
                  <button onClick={() => setAiAnswer(null)} className="text-[10px] text-muted-foreground hover:text-foreground">Dismiss</button>
                </div>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{aiAnswer}</p>
              </div>
            )}
          </div>

          {/* Event type filter bar */}
          {!searchActive && (
            <div className="flex items-center gap-3 mb-3 flex-shrink-0">
              <div className="flex gap-1">
                {([["all", "All"], ["recordings", "Recordings"], ["observations", "AI"], ["status", "Status"]] as [EventFilter, string][]).map(([v, l]) => (
                  <button key={v} onClick={() => setEventFilter(v)}
                    className={`px-2 py-1 text-[11px] rounded transition-colors ${eventFilter === v ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                    {l}
                  </button>
                ))}
              </div>
              <div className="flex-1" />
              <button onClick={fetchDigest} disabled={digestLoading}
                className="px-2 py-1 text-[10px] rounded border border-border text-muted-foreground hover:bg-muted disabled:opacity-50">
                {digestLoading ? "..." : "Digest"}
              </button>
            </div>
          )}

          {/* Digest panel */}
          {digest && digest.total_observations > 0 && !searchActive && (
            <div className="rounded-md border border-border bg-card/50 p-3 mb-3 flex-shrink-0">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Activity Digest</span>
                <span className="text-[10px] text-muted-foreground font-mono">{digest.period_label}</span>
              </div>
              <p className="text-xs leading-relaxed">{digest.summary}</p>
              {digest.highlights.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {digest.highlights.slice(0, 3).map((h, i) => <div key={i} className="text-[11px] text-muted-foreground">{h}</div>)}
                </div>
              )}
            </div>
          )}

          {/* Live event toasts */}
          {!searchActive && liveEvents.length > 0 && (
            <div className="mb-3 space-y-1 flex-shrink-0">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-medium text-accent uppercase tracking-wider flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" /> Live
                </span>
                <button onClick={() => setLiveEvents([])} className="text-[10px] text-muted-foreground hover:text-foreground">clear</button>
              </div>
              {liveEvents.slice(0, 3).map((evt, i) => (
                <div key={i} className="px-3 py-1.5 rounded-md border border-accent/30 bg-accent/5 text-xs flex items-center justify-between">
                  <span>{evt.message || `Rule "${evt.rule_name}" fired`}</span>
                  <span className="text-[10px] text-muted-foreground font-mono">{evt.timestamp ? formatTime(evt.timestamp) : "now"}</span>
                </div>
              ))}
            </div>
          )}

          {/* Timeline feed (scrollable) */}
          <div className="flex-1 overflow-y-auto scrollbar-thin pr-1">
            {isSearching ? (
              <div className="flex items-center justify-center py-20"><div className="text-sm text-muted-foreground">Searching.</div></div>
            ) : timelineLoading && entries.length === 0 ? (
              <div className="flex items-center justify-center py-20"><div className="text-sm text-muted-foreground">Loading.</div></div>
            ) : entries.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <p className="text-muted-foreground text-sm">
                  {searchActive ? "No observations match your search." : "No events in this time range."}
                </p>
              </div>
            ) : (
              <div className="space-y-5">
                {Object.entries(grouped).map(([date, dateEntries]) => (
                  <div key={date}>
                    <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2 sticky top-0 bg-background/80 backdrop-blur-sm py-1 z-10">{date}</div>
                    <div className="space-y-1.5">
                      {dateEntries.map((entry) => {
                        const cam = cameraMap[entry.camera_id];
                        const isActive = activeEntry === entry.id;

                        if (entry.type === "search_result") {
                          const r = entry.data as SearchResult;
                          const srFaces = r.person_detections?.faces || [];
                          const srNamed = srFaces.filter((f) => f.person_name);
                          const srUnknown = srFaces.filter((f) => !f.person_name);
                          const srObjects = r.object_detections?.objects?.filter((d) => d.label !== "person" && d.label !== "license_plate") || [];
                          const srPlates = r.object_detections?.objects?.filter((d) => d.label === "license_plate" && d.plate_text) || [];
                          return (
                            <div key={entry.id}>
                              <button onClick={() => setActiveEntry(isActive ? null : entry.id)}
                                className={`w-full text-left rounded-lg border transition-colors overflow-hidden ${isActive ? "border-accent bg-card" : "border-border hover:border-accent/50 hover:bg-card/50"}`}>
                                <div className="flex gap-3">
                                  {r.thumbnail_path && (
                                    <div className="w-20 h-16 flex-shrink-0 bg-black/50 overflow-hidden">
                                      <img src={`/api/observations/${r.id}/thumbnail`} alt="" className="w-full h-full object-cover" />
                                    </div>
                                  )}
                                  <div className={`flex-1 min-w-0 py-2 ${r.thumbnail_path ? "pr-3" : "px-3"}`}>
                                    <div className="flex items-start justify-between gap-2">
                                      <div className="min-w-0 flex-1">
                                        {srFaces.length > 0 ? (
                                          <div className="flex flex-wrap items-center gap-1">
                                            {srNamed.map((f, i) => <span key={`n${i}`} className="text-xs font-medium text-green-400">{f.person_name}</span>)}
                                            {srNamed.length > 0 && srUnknown.length > 0 && <span className="text-[10px] text-muted-foreground">+</span>}
                                            {srUnknown.length > 0 && <span className="text-xs text-yellow-400">{srUnknown.length === 1 ? "unknown person" : `${srUnknown.length} unknown`}</span>}
                                          </div>
                                        ) : (
                                          <p className="text-xs font-medium line-clamp-1">
                                            {r.vlm_description ? r.vlm_description.split(/\.\s/)[0].slice(0, 80) : "Motion detected"}
                                          </p>
                                        )}
                                        <div className="flex flex-wrap items-center gap-1 mt-1">
                                          {srObjects.slice(0, 4).map((obj, i) => (
                                            <span key={i} className="px-1 py-0.5 text-[9px] rounded bg-blue-900/30 text-blue-300 border border-blue-800/40">{obj.label}</span>
                                          ))}
                                          {srPlates.map((d, i) => (
                                            <span key={`p${i}`} className="px-1 py-0.5 text-[9px] rounded bg-accent/20 text-accent border border-accent/40">{d.plate_text}</span>
                                          ))}
                                          <span className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{r.camera_name || cam?.name || "Unknown"}</span>
                                        </div>
                                      </div>
                                      <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0 pt-0.5">{formatTime(r.started_at)}</span>
                                    </div>
                                  </div>
                                </div>
                              </button>
                              {isActive && (
                                <div className="mt-1.5 rounded-lg border border-border bg-card p-3 space-y-2">
                                  {r.thumbnail_path && (
                                    <div className="rounded-lg overflow-hidden border border-border">
                                      <img src={`/api/observations/${r.id}/thumbnail`} alt="" className="w-full" />
                                    </div>
                                  )}
                                  {r.vlm_description && <p className="text-xs leading-relaxed">{r.vlm_description}</p>}
                                </div>
                              )}
                            </div>
                          );
                        }

                        if (entry.type === "status") {
                          const log = entry.data as StatusLog;
                          const isOnline = log.status === "live" || log.status === "recording";
                          return (
                            <div key={entry.id} className="px-3 py-2 rounded-lg border border-border/50 flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <div className={`w-1.5 h-1.5 rounded-full ${statusColor(log.status)}`} />
                                <span className="text-xs"><span className="font-medium">{cam?.name || "Unknown"}</span><span className="mx-1 text-muted-foreground">{log.status === "recording" ? "started" : log.status === "offline" ? "went" : "is"}</span><span className={isOnline ? "text-green-400" : "text-muted-foreground"}>{log.status === "recording" ? "recording" : statusLabel(log.status).toLowerCase()}</span></span>
                              </div>
                              <span className="text-[10px] text-muted-foreground font-mono">{formatTime(log.timestamp)}</span>
                            </div>
                          );
                        }

                        if (entry.type === "recording") {
                          const rec = entry.data as Recording;
                          return (
                            <div key={entry.id}>
                              <button onClick={() => setActiveEntry(isActive ? null : entry.id)}
                                className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors ${isActive ? "border-accent bg-card" : "border-border hover:border-accent/50 hover:bg-card/50"}`}>
                                <div className="flex items-center justify-between">
                                  <div className="flex items-center gap-2">
                                    <div className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                                    <div>
                                      <div className="text-xs font-medium">
                                        Recording
                                        <span className="ml-1.5 font-normal text-muted-foreground">{formatDuration(rec.duration_seconds)}</span>
                                        {rec.file_size_bytes && <span className="ml-1 font-normal text-muted-foreground">{formatSize(rec.file_size_bytes)}</span>}
                                      </div>
                                      <div className="flex items-center gap-1 mt-0.5">
                                        <span className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{cam?.name || "Unknown"}</span>
                                        <span className="font-mono text-[10px] text-muted-foreground">{formatTime(rec.started_at)}{rec.ended_at && ` \u2192 ${formatTime(rec.ended_at)}`}</span>
                                      </div>
                                    </div>
                                  </div>
                                </div>
                              </button>
                              {isActive && (
                                <div className="mt-1.5 rounded-lg overflow-hidden border border-border bg-black">
                                  <video controls autoPlay className="w-full aspect-video" src={`/api/recordings/${rec.id}/stream`} />
                                </div>
                              )}
                            </div>
                          );
                        }

                        // Observation
                        const obs = entry.data as Observation;
                        const hasThumb = !!obs.thumbnail_path;
                        const hasFaces = obs.person_detections?.faces && obs.person_detections.faces.length > 0;
                        const namedFaces = obs.person_detections?.faces?.filter((f) => f.person_name) || [];
                        const unknownFaces = obs.person_detections?.faces?.filter((f) => !f.person_name) || [];
                        const objects = obs.object_detections?.objects?.filter((d) => d.label !== "person" && d.label !== "license_plate") || [];
                        const plates = obs.object_detections?.objects?.filter((d) => d.label === "license_plate" && d.plate_text) || [];

                        return (
                          <div key={entry.id}>
                            <button onClick={() => setActiveEntry(isActive ? null : entry.id)}
                              className={`w-full text-left rounded-lg border transition-colors overflow-hidden ${isActive ? "border-accent bg-card" : "border-border hover:border-accent/50 hover:bg-card/50"}`}>
                              <div className="flex gap-3">
                                {/* Inline thumbnail */}
                                {hasThumb && (
                                  <div className="w-20 h-16 flex-shrink-0 bg-black/50 overflow-hidden">
                                    <img src={`/api/observations/${obs.id}/thumbnail`} alt="" className="w-full h-full object-cover" />
                                  </div>
                                )}
                                <div className={`flex-1 min-w-0 py-2 ${hasThumb ? "pr-3" : "px-3"}`}>
                                  {/* Primary line. detection summary */}
                                  <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0 flex-1">
                                      {/* Person names as headline */}
                                      {hasFaces ? (
                                        <div className="flex flex-wrap items-center gap-1">
                                          {namedFaces.map((f, i) => (
                                            <span key={`n${i}`} className="text-xs font-medium text-green-400">{f.person_name}</span>
                                          ))}
                                          {namedFaces.length > 0 && unknownFaces.length > 0 && <span className="text-[10px] text-muted-foreground">+</span>}
                                          {unknownFaces.length > 0 && (
                                            <span className="text-xs text-yellow-400">{unknownFaces.length === 1 ? "unknown person" : `${unknownFaces.length} unknown`}</span>
                                          )}
                                        </div>
                                      ) : (
                                        <p className="text-xs font-medium line-clamp-1">
                                          {obs.vlm_description
                                            ? obs.vlm_description.split(/\.\s/)[0].slice(0, 80)
                                            : summarizeDetections(obs)}
                                        </p>
                                      )}

                                      {/* Detection tags */}
                                      <div className="flex flex-wrap items-center gap-1 mt-1">
                                        {objects.slice(0, 4).map((d, i) => (
                                          <span key={i} className="px-1 py-0.5 text-[9px] rounded bg-blue-900/30 text-blue-300 border border-blue-800/40">{d.label}</span>
                                        ))}
                                        {objects.length > 4 && <span className="text-[9px] text-muted-foreground">+{objects.length - 4}</span>}
                                        {plates.map((d, i) => (
                                          <span key={`p${i}`} className="px-1 py-0.5 text-[9px] rounded bg-accent/20 text-accent border border-accent/40">{d.plate_text}</span>
                                        ))}
                                        {/* Camera name as subtle tag */}
                                        <span className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{cam?.name || "Unknown"}</span>
                                      </div>
                                    </div>
                                    <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0 pt-0.5">{formatTime(obs.started_at)}</span>
                                  </div>
                                </div>
                              </div>
                            </button>
                            {isActive && (
                              <div className="mt-1.5 rounded-lg border border-border bg-card p-3 space-y-2">
                                {/* Full thumbnail if not shown inline, or larger version */}
                                {hasThumb && (
                                  <div className="rounded-lg overflow-hidden border border-border">
                                    <img src={`/api/observations/${obs.id}/thumbnail`} alt="" className="w-full" />
                                  </div>
                                )}
                                {obs.vlm_description && (
                                  <p className="text-xs leading-relaxed">{obs.vlm_description}</p>
                                )}
                                {hasFaces && (
                                  <div className="flex flex-wrap gap-1">
                                    {obs.person_detections!.faces!.map((f, i) => (
                                      <span key={`f${i}`} className={`px-1.5 py-0.5 text-[10px] rounded-full border ${f.person_name ? "bg-green-900/30 text-green-300 border-green-800/40" : "bg-yellow-900/30 text-yellow-300 border-yellow-800/40"}`}>
                                        {f.person_name || "Unknown person"}{f.match_distance != null && <span className="ml-1 text-muted-foreground">{((1 - f.match_distance) * 100).toFixed(0)}%</span>}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {obs.object_detections && obs.object_detections.count > 0 && (
                                  <div className="flex flex-wrap gap-1">
                                    {obs.object_detections.objects.map((d, i) => (
                                      <span key={i} className={`px-1.5 py-0.5 text-[10px] rounded-full border ${d.label === "license_plate" ? "bg-accent/20 text-accent border-accent/40" : "bg-muted border-border"}`}>
                                        {d.label === "license_plate" && d.plate_text ? `plate ${d.plate_text}` : d.label} <span className="text-muted-foreground">{(d.confidence * 100).toFixed(0)}%</span>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                                  <span>{cam?.name || "Unknown"}</span>
                                  {obs.vlm_provider && <span className="font-mono">via {obs.vlm_provider}</span>}
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
          </div>
        </main>
      </div>

      {modalOpen && <AddCameraModal onClose={() => setModalOpen(false)} onSuccess={() => { setModalOpen(false); fetchCameras(); }} />}
    </div>
  );
}

export default function HomePage() {
  const { authFetch } = useAuth();
  return (
    <Suspense>
      <DashboardContent />
    </Suspense>
  );
}
