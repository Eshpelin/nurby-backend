"use client";

import { Suspense, useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useWebcamPublisher, listVideoDevices } from "@/lib/webcam-publisher";

const WEBRTC_URL =
  process.env.NEXT_PUBLIC_WEBRTC_URL || "http://localhost:8889";

type StreamType = "rtsp" | "http_mjpeg" | "http_snapshot" | "hls" | "usb" | "file" | "webcam";

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

interface PersonSummary {
  person_id: string;
  display_name: string;
  relationship: string | null;
  photo_path: string | null;
  total_sightings: number;
  sightings_1h: number;
  sightings_24h: number;
  last_seen_at: string | null;
  last_seen_camera: string | null;
  first_seen_at: string | null;
}

interface ClusterSummary {
  cluster_id: string;
  auto_label: string;
  auto_label_number: number | null;
  appearance_description: string | null;
  appearance_description_status: string;
  sample_thumbnail_path: string | null;
  sighting_count: number;
  sightings_1h: number;
  sightings_24h: number;
  last_seen_at: string | null;
  last_seen_camera: string | null;
  first_seen_at: string | null;
}

interface PersonActivityItem {
  observation_id: string;
  camera_id: string;
  camera_name: string | null;
  started_at: string;
  ended_at: string | null;
  vlm_description: string | null;
  thumbnail_path: string | null;
  person_name: string | null;
  match_distance: number | null;
  object_detections: { objects?: { label: string; confidence: number }[] } | null;
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

const STREAM_TYPES: { value: StreamType; label: string; hint: string; placeholder: string }[] = [
  { value: "webcam", label: "This Device", hint: "Use your laptop or phone webcam as a test camera", placeholder: "" },
  { value: "rtsp", label: "RTSP", hint: "IP cameras, NVRs, most security cameras", placeholder: "rtsp://192.168.1.100:554/stream1" },
  { value: "http_mjpeg", label: "HTTP MJPEG", hint: "Motion JPEG over HTTP. Webcams, ESP32-CAM", placeholder: "http://192.168.1.100:8080/video" },
  { value: "http_snapshot", label: "HTTP Snapshot", hint: "Periodic JPEG pull. Low-bandwidth cameras", placeholder: "http://192.168.1.100/snapshot.jpg" },
  { value: "hls", label: "HLS", hint: "HTTP Live Streaming. Cloud cameras, Wyze, Ring", placeholder: "http://192.168.1.100/live/stream.m3u8" },
  { value: "usb", label: "USB / Local", hint: "Locally attached USB or CSI cameras", placeholder: "0" },
  { value: "file", label: "File / Test", hint: "Local video file for testing", placeholder: "/path/to/video.mp4" },
];

type TimeRange = "today" | "7d" | "30d";
type EventFilter = "recordings" | "observations" | "status";

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

function hourBucketKey(iso: string): string {
  const d = new Date(iso);
  d.setMinutes(0, 0, 0);
  return d.toISOString();
}

function formatHourBucket(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const today = new Date(now); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
  const bucketDay = new Date(d); bucketDay.setHours(0, 0, 0, 0);
  const hr = d.toLocaleTimeString([], { hour: "numeric", hour12: true });
  let day = "";
  if (bucketDay.getTime() === today.getTime()) day = "Today";
  else if (bucketDay.getTime() === yesterday.getTime()) day = "Yesterday";
  else day = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return `${day} \u00b7 ${hr}`;
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
      parts.push(unnamed.length === 1 ? "Unknown person" : `${unnamed.length} unknown people`);
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

function PersonActivityModal({ personId, personName, onClose, mode = "person" }: { personId: string; personName: string; onClose: () => void; mode?: "person" | "cluster" }) {
  const { authFetch } = useAuth();
  const [items, setItems] = useState<PersonActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [cameraMap, setCameraMap] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const activityUrl = mode === "cluster"
          ? `/api/persons/clusters/activity/${personId}?limit=200`
          : `/api/persons/activity/${personId}?limit=200`;
        const [actRes, camRes] = await Promise.all([
          authFetch(activityUrl),
          authFetch(`/api/cameras`),
        ]);
        if (cancelled) return;
        if (actRes.ok) {
          const all: PersonActivityItem[] = await actRes.json();
          // Filter to last 24h
          const cutoff = Date.now() - 24 * 3600 * 1000;
          setItems(all.filter((i) => i.started_at && new Date(i.started_at).getTime() >= cutoff));
        }
        if (camRes.ok) {
          const cams: Camera[] = await camRes.json();
          setCameraMap(Object.fromEntries(cams.map((c) => [c.id, c.name])));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [personId, authFetch, mode]);

  // Build per-visit sessions (gap > 10 min = new visit)
  const sessions: { start: string; end: string; cameras: Set<string>; items: PersonActivityItem[] }[] = [];
  const sortedAsc = [...items].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  const SESSION_GAP_MS = 10 * 60 * 1000;
  for (const item of sortedAsc) {
    const t = new Date(item.started_at).getTime();
    const last = sessions[sessions.length - 1];
    if (!last || t - new Date(last.end).getTime() > SESSION_GAP_MS) {
      sessions.push({ start: item.started_at, end: item.ended_at || item.started_at, cameras: new Set([item.camera_id]), items: [item] });
    } else {
      last.end = item.ended_at || item.started_at;
      last.cameras.add(item.camera_id);
      last.items.push(item);
    }
  }
  sessions.reverse(); // show most recent first

  const totalEvents = items.length;
  const totalCams = new Set(items.map((i) => i.camera_id)).size;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-2xl mx-4 rounded-xl border border-border bg-card-elevated shadow-2xl max-h-[85vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-10 h-10 rounded-full overflow-hidden border border-border bg-muted flex-shrink-0">
              <img src={`/api/persons/${personId}/photo`} alt={personName} className="w-full h-full object-cover"
                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
            </div>
            <div className="min-w-0">
              <h2 className="text-base font-semibold truncate">{personName}</h2>
              <div className="text-[11px] text-muted-foreground">Activity in the last 24 hours</div>
            </div>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-xl leading-none">&times;</button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-sm text-muted-foreground">Loading activity.</div>
        ) : sessions.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-sm text-muted-foreground">No sightings of {personName} in the last 24 hours.</p>
          </div>
        ) : (
          <div className="overflow-y-auto scrollbar-thin">
            {/* Stats strip */}
            <div className="grid grid-cols-3 gap-2 px-5 py-3 border-b border-border bg-card/30">
              <div>
                <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Visits</div>
                <div className="text-sm font-semibold">{sessions.length}</div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Events</div>
                <div className="text-sm font-semibold">{totalEvents}</div>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Cameras</div>
                <div className="text-sm font-semibold">{totalCams}</div>
              </div>
            </div>

            {/* Visits / sessions */}
            <div className="p-5 space-y-4">
              {sessions.map((s, i) => {
                const start = new Date(s.start);
                const end = new Date(s.end);
                const durMin = Math.max(1, Math.round((end.getTime() - start.getTime()) / 60000));
                const camNames = Array.from(s.cameras).map((id) => cameraMap[id] || "Unknown");
                return (
                  <div key={i} className="rounded-lg border border-border bg-card/50 overflow-hidden">
                    <div className="px-3 py-2 border-b border-border/50 flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold">
                          {start.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}
                          {" \u2192 "}
                          {end.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                          {durMin} min \u00b7 {camNames.join(", ")}
                        </div>
                      </div>
                      <span className="text-[10px] text-muted-foreground">{s.items.length} event{s.items.length > 1 ? "s" : ""}</span>
                    </div>
                    <div className="divide-y divide-border/50">
                      {s.items.slice().reverse().map((it) => (
                        <div key={it.observation_id} className="flex gap-3 p-2.5">
                          {it.thumbnail_path ? (
                            <img src={`/api/observations/${it.observation_id}/thumbnail`} alt=""
                              className="w-20 h-14 flex-shrink-0 rounded object-cover bg-black" />
                          ) : (
                            <div className="w-20 h-14 flex-shrink-0 rounded bg-muted" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-2">
                              <p className="text-xs leading-snug line-clamp-2">
                                {it.vlm_description || "Motion detected"}
                              </p>
                              <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0">
                                {formatTime(it.started_at)}
                              </span>
                            </div>
                            <div className="flex flex-wrap gap-1 mt-1">
                              <span className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{it.camera_name || cameraMap[it.camera_id] || "Unknown"}</span>
                              {(it.object_detections?.objects || []).slice(0, 3).map((d, di) => (
                                <span key={di} className="px-1 py-0.5 text-[9px] rounded bg-blue-900/30 text-blue-300 border border-blue-800/40">{d.label}</span>
                              ))}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AddCameraModal({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const { authFetch } = useAuth();
  const { startPublish, attachCameraId, stopPublish } = useWebcamPublisher();
  const [activeTab, setActiveTab] = useState<ModalTab>("manual");
  const [name, setName] = useState("");
  const [streamType, setStreamType] = useState<StreamType>("rtsp");
  const [streamUrl, setStreamUrl] = useState("");

  // Webcam state
  const [webcamDevices, setWebcamDevices] = useState<MediaDeviceInfo[]>([]);
  const [webcamDeviceId, setWebcamDeviceId] = useState<string>("");
  const [webcamStream, setWebcamStream] = useState<MediaStream | null>(null);
  const [webcamError, setWebcamError] = useState<string | null>(null);
  const webcamPreviewRef = useRef<HTMLVideoElement | null>(null);
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

  // Load devices when webcam mode enters
  useEffect(() => {
    if (streamType !== "webcam") return;
    let cancelled = false;
    (async () => {
      try {
        const list = await listVideoDevices();
        if (cancelled) return;
        setWebcamDevices(list);
        if (list.length && !webcamDeviceId) setWebcamDeviceId(list[0].deviceId);
      } catch (err) {
        setWebcamError(err instanceof Error ? err.message : "Unable to list cameras");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamType]);

  // Start preview when device selection changes (in webcam mode)
  useEffect(() => {
    if (streamType !== "webcam" || !webcamDeviceId) return;
    let active = true;
    let newStream: MediaStream | null = null;
    setWebcamError(null);
    (async () => {
      try {
        newStream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: webcamDeviceId } },
          audio: false,
        });
        if (!active) { newStream.getTracks().forEach((t) => t.stop()); return; }
        setWebcamStream((prev) => {
          prev?.getTracks().forEach((t) => t.stop());
          return newStream;
        });
      } catch (err) {
        setWebcamError(err instanceof Error ? err.message : "Camera access denied");
      }
    })();
    return () => {
      active = false;
      // Don't stop the current stream here; replacement handled above
    };
  }, [streamType, webcamDeviceId]);

  // Attach stream to preview <video>
  useEffect(() => {
    if (webcamPreviewRef.current && webcamStream) {
      webcamPreviewRef.current.srcObject = webcamStream;
    }
  }, [webcamStream]);

  // Cleanup preview on close or type switch away
  useEffect(() => {
    return () => {
      webcamStream?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleWebcamSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !webcamStream) return;
    setSubmitting(true);
    setError(null);
    // Generate a URL-safe stream path
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "webcam";
    const streamPath = `${slug}-${Math.random().toString(36).slice(2, 8)}`;
    try {
      await startPublish({ streamPath, cameraName: name.trim(), stream: webcamStream });
      // Hand ownership of the stream to the publisher so modal cleanup won't stop it
      setWebcamStream(null);
      const payload: Record<string, unknown> = {
        name: name.trim(),
        stream_url: `rtsp://localhost:8554/${streamPath}`,
        stream_type: "webcam",
        location_label: locationLabel.trim() || null,
      };
      const res = await authFetch(`/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        stopPublish(streamPath);
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Request failed with status ${res.status}`);
      }
      const created = await res.json().catch(() => null);
      if (created?.id) attachCameraId(streamPath, created.id);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start webcam");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleManualSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (streamType === "webcam") return handleWebcamSubmit(e);
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

            {streamType === "webcam" ? (
              <div>
                <label className="block text-sm text-muted-foreground mb-1.5">Camera Device</label>
                {webcamDevices.length > 0 ? (
                  <select value={webcamDeviceId} onChange={(e) => setWebcamDeviceId(e.target.value)} className={inputClass}>
                    {webcamDevices.map((d, i) => (
                      <option key={d.deviceId} value={d.deviceId}>{d.label || `Camera ${i + 1}`}</option>
                    ))}
                  </select>
                ) : (
                  <p className="text-[11px] text-muted-foreground">Requesting camera access...</p>
                )}
                {webcamError && <p className="text-[11px] text-danger mt-1">{webcamError}</p>}
                <div className="mt-3 rounded-md overflow-hidden border border-border bg-black aspect-video">
                  {webcamStream ? (
                    <video ref={webcamPreviewRef} autoPlay muted playsInline className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[11px] text-muted-foreground">No preview</div>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1.5">Stream stays live while this tab is open. Closing the tab stops it.</p>
              </div>
            ) : (
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
            )}

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
              <button type="submit" disabled={submitting || !name.trim() || (streamType === "webcam" ? !webcamStream : !streamUrl.trim())} className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
                {submitting ? "Adding..." : streamType === "webcam" ? "Start Streaming" : "Add Camera"}
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
  const [eventFilters, setEventFilters] = useState<Set<EventFilter>>(new Set(["recordings", "observations", "status"]));
  const [timelineLoading, setTimelineLoading] = useState(true);

  // Filter modal state
  const [filterModalOpen, setFilterModalOpen] = useState(false);

  // Hourly digest bucket expand state
  const [expandedBuckets, setExpandedBuckets] = useState<Set<string>>(new Set());

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchActive, setSearchActive] = useState(false);
  const [filterPerson, setFilterPerson] = useState("");
  const [filterObject, setFilterObject] = useState("");
  const [aiAnswer, setAiAnswer] = useState<string | null>(null);
  const [askingAi, setAskingAi] = useState(false);
  const [hasAiProvider, setHasAiProvider] = useState<boolean | null>(null);

  // Live events
  const [liveEvents, setLiveEvents] = useState<{ type: string; rule_name?: string; camera_id?: string; timestamp?: string; message?: string }[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Digest
  const [digest, setDigest] = useState<Digest | null>(null);
  const [digestPeriod, setDigestPeriod] = useState<"daily" | "hourly">("hourly");
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

  // 24h person digest
  const [personSummaries, setPersonSummaries] = useState<PersonSummary[]>([]);
  const [clusterSummaries, setClusterSummaries] = useState<ClusterSummary[]>([]);
  const [personSummariesLoading, setPersonSummariesLoading] = useState(false);
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);

  const fetchPersonSummaries = useCallback(async () => {
    setPersonSummariesLoading(true);
    try {
      const [personRes, clusterRes] = await Promise.all([
        authFetch("/api/persons/activity/summary"),
        authFetch("/api/persons/clusters/activity/summary?hours=24&min_sightings=2"),
      ]);
      if (personRes.ok) setPersonSummaries(await personRes.json());
      if (clusterRes.ok) setClusterSummaries(await clusterRes.json());
    } catch { /* silent */ }
    finally { setPersonSummariesLoading(false); }
  }, [authFetch]);

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
  }, [digestPeriod, selectedCamera, authFetch]);

  // Always auto-fetch digest on mount and when period/camera changes
  useEffect(() => {
    fetchDigest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digestPeriod, selectedCamera]);

  // Fetch person summaries for the 24h person digest
  useEffect(() => {
    if (digestPeriod === "daily") fetchPersonSummaries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digestPeriod]);

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

  const clearSearch = () => { setSearchQuery(""); setSearchActive(false); setSearchResults([]); setAiAnswer(null); };

  const clearAllFilters = () => {
    setTimeRange("7d");
    setEventFilters(new Set(["recordings", "observations", "status"]));
    setFilterPerson("");
    setFilterObject("");
    setSelectedCamera(null);
  };

  const toggleEventFilter = (f: EventFilter) => {
    setEventFilters((prev) => {
      const next = new Set(prev);
      if (next.has(f)) { next.delete(f); } else { next.add(f); }
      return next;
    });
  };

  const activeFilterCount = [
    filterPerson,
    filterObject,
    timeRange !== "7d" ? "active" : "",
    eventFilters.size < 3 ? "active" : "",
  ].filter(Boolean).length;

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
    if (eventFilters.has("recordings")) entries.push(...recordings.map((r) => ({ id: `rec-${r.id}`, type: "recording" as const, camera_id: r.camera_id, timestamp: r.started_at, data: r })));
    if (eventFilters.has("observations")) entries.push(...observations.map((o) => ({ id: `obs-${o.id}`, type: "observation" as const, camera_id: o.camera_id, timestamp: o.started_at, data: o })));
    if (eventFilters.has("status")) entries.push(...statusLogs.map((s) => ({ id: `status-${s.id}`, type: "status" as const, camera_id: s.camera_id, timestamp: s.timestamp, data: s })));
  }

  entries.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  // Group by hour bucket for digest-style view
  const hourGroups: { key: string; entries: TimelineEntry[] }[] = [];
  const hourMap: Record<string, TimelineEntry[]> = {};
  for (const e of entries) {
    const k = searchActive ? "search" : hourBucketKey(e.timestamp);
    if (!hourMap[k]) { hourMap[k] = []; hourGroups.push({ key: k, entries: hourMap[k] }); }
    hourMap[k].push(e);
  }

  // Build a lightweight digest for a bucket's entries
  function bucketDigest(bucketEntries: TimelineEntry[]) {
    let obsCount = 0, recCount = 0, statusCount = 0;
    const persons = new Set<string>();
    let unknownFaces = 0;
    const objectCounts: Record<string, number> = {};
    const camCounts: Record<string, number> = {};
    let plateCount = 0;
    for (const e of bucketEntries) {
      camCounts[e.camera_id] = (camCounts[e.camera_id] || 0) + 1;
      if (e.type === "recording") recCount++;
      else if (e.type === "status") statusCount++;
      else if (e.type === "observation" || e.type === "search_result") {
        obsCount++;
        const o = e.data as Observation;
        for (const f of o.person_detections?.faces || []) {
          if (f.person_name) persons.add(f.person_name); else unknownFaces++;
        }
        for (const d of o.object_detections?.objects || []) {
          if (d.label === "license_plate") plateCount++;
          else if (d.label !== "person") objectCounts[d.label] = (objectCounts[d.label] || 0) + 1;
        }
      }
    }
    const topObjects = Object.entries(objectCounts).sort((a, b) => b[1] - a[1]).slice(0, 4);
    const topCams = Object.entries(camCounts).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([id, n]) => ({ name: cameraMap[id]?.name || "Unknown", n }));
    const bits: string[] = [];
    if (persons.size) bits.push(`${Array.from(persons).join(", ")} seen`);
    if (unknownFaces) bits.push(`${unknownFaces} unknown ${unknownFaces === 1 ? "face" : "faces"}`);
    if (topObjects.length) bits.push(topObjects.map(([l, n]) => `${n} ${l}${n > 1 ? "s" : ""}`).join(", "));
    if (plateCount) bits.push(`${plateCount} plate${plateCount > 1 ? "s" : ""}`);
    if (recCount) bits.push(`${recCount} recording${recCount > 1 ? "s" : ""}`);
    return {
      total: bucketEntries.length,
      obsCount, recCount, statusCount,
      summary: bits.length ? bits.join(" \u00b7 ") : `${bucketEntries.length} event${bucketEntries.length > 1 ? "s" : ""}`,
      topCams,
      persons: Array.from(persons),
      unknownFaces,
    };
  }


  return (
    <div className="px-4 py-4 h-[calc(100vh-3.5rem)] flex flex-col">

      <div className="flex gap-4 flex-1 min-h-0">
        {/* LEFT. Camera feeds */}
        <aside className={`flex-shrink-0 flex flex-col min-h-0 transition-all ${
          cameraLayout === "double" ? "w-[480px]" : cameraLayout === "list" ? "w-80" : "w-72"
        }`}>
          {/* Camera list header with layout toggle */}
          <div className="flex items-center justify-between mb-2 flex-shrink-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">Cameras</span>
              {cameras.length > 0 && (
                <button onClick={() => setModalOpen(true)}
                  className="w-4 h-4 rounded flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                  title="Add camera">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M12 5v14" /><path d="M5 12h14" />
                  </svg>
                </button>
              )}
            </div>
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
              <div className={`rounded-lg border border-dashed border-border bg-card/30 p-4 ${cameraLayout === "double" ? "col-span-2" : ""}`}>
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-8 h-8 rounded-full bg-accent/10 border border-accent/30 flex items-center justify-center text-accent">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                    </svg>
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs font-semibold">No cameras yet</div>
                    <div className="text-[11px] text-muted-foreground leading-tight">Connect a feed to start capturing activity.</div>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <button onClick={() => setModalOpen(true)}
                    className="w-full px-2.5 py-2 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity">
                    Add a camera
                  </button>
                  <button onClick={() => setModalOpen(true)}
                    className="w-full px-2.5 py-2 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
                    Use this device's webcam
                  </button>
                </div>
                <div className="mt-3 pt-3 border-t border-border/50 text-[10px] text-muted-foreground leading-relaxed">
                  <div className="font-medium text-foreground/70 mb-1">Options</div>
                  <ul className="space-y-0.5">
                    <li>RTSP/HTTP from IP cameras and NVRs</li>
                    <li>ONVIF network auto-discovery</li>
                    <li>USB or local device testing</li>
                  </ul>
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Filter modal */}
        {filterModalOpen && (
          <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]" onClick={() => setFilterModalOpen(false)}>
            <div className="fixed inset-0 bg-black/60" />
            <div className="relative w-full max-w-md rounded-xl border border-border bg-card shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between px-5 py-4 border-b border-border">
                <span className="text-sm font-medium">Filters</span>
                <div className="flex items-center gap-3">
                  {activeFilterCount > 0 && (
                    <button onClick={clearAllFilters} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
                      Clear all
                    </button>
                  )}
                  <button onClick={() => setFilterModalOpen(false)} className="text-muted-foreground hover:text-foreground transition-colors">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M18 6 6 18" /><path d="m6 6 12 12" />
                    </svg>
                  </button>
                </div>
              </div>

              <div className="p-5 space-y-5">
                {/* Time Range */}
                <div>
                  <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Time Range</span>
                  <div className="flex gap-2">
                    {(["today", "7d", "30d"] as TimeRange[]).map((range) => (
                      <button key={range} onClick={() => setTimeRange(range)}
                        className={`flex-1 px-3 py-2 text-xs rounded-lg transition-colors ${timeRange === range ? "bg-accent/15 text-accent-foreground font-medium border border-accent/30" : "text-muted-foreground border border-border hover:text-foreground hover:bg-muted/50"}`}>
                        {range === "today" ? "Today" : range === "7d" ? "7 days" : "30 days"}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Event Types */}
                <div>
                  <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Event Types</span>
                  <div className="flex flex-col gap-1">
                    {([["recordings", "Recordings"], ["observations", "AI Observations"], ["status", "Status Changes"]] as [EventFilter, string][]).map(([value, label]) => (
                      <label key={value} className="flex items-center gap-2.5 px-3 py-2 text-xs rounded-lg hover:bg-muted/50 cursor-pointer transition-colors">
                        <input type="checkbox" checked={eventFilters.has(value)} onChange={() => toggleEventFilter(value)}
                          className="w-3.5 h-3.5 rounded border-border accent-accent" />
                        <span className={eventFilters.has(value) ? "text-foreground" : "text-muted-foreground"}>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  {/* Person Filter */}
                  <div>
                    <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Person</span>
                    <select value={filterPerson} onChange={(e) => { setFilterPerson(e.target.value); if (e.target.value) handleSearch(); }}
                      className="w-full px-3 py-2 rounded-lg bg-background border border-border text-xs focus:outline-none focus:ring-1 focus:ring-accent">
                      <option value="">Any person</option>
                      {persons.map((p) => <option key={p.id} value={p.display_name}>{p.display_name}</option>)}
                    </select>
                  </div>

                  {/* Object Filter */}
                  <div>
                    <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Object</span>
                    <input type="text" value={filterObject} onChange={(e) => setFilterObject(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { handleSearch(); setFilterModalOpen(false); } }}
                      placeholder="e.g. car, dog"
                      className="w-full px-3 py-2 rounded-lg bg-background border border-border text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent" />
                  </div>
                </div>

                {/* Camera Filter */}
                <div>
                  <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Camera</span>
                  <select value={selectedCamera || ""} onChange={(e) => setSelectedCamera(e.target.value || null)}
                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-xs focus:outline-none focus:ring-1 focus:ring-accent">
                    <option value="">All cameras</option>
                    {cameras.map((cam) => <option key={cam.id} value={cam.id}>{cam.name}</option>)}
                  </select>
                </div>
              </div>

              <div className="px-5 py-4 border-t border-border">
                <button onClick={() => { handleSearch(); setFilterModalOpen(false); }}
                  className="w-full py-2.5 text-xs font-medium rounded-lg bg-accent text-black hover:bg-accent/90 transition-colors">
                  Apply Filters
                </button>
              </div>
            </div>
          </div>
        )}

        {/* RIGHT. Timeline + Search */}
        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Search bar */}
          <div className="flex-shrink-0 mb-3">
            <div className="flex gap-2">
              <div className="relative flex-1">
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
                  {!isSearching && searchQuery.trim() && !searchActive && (
                    <button onClick={handleSearch} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground hover:bg-border">search</button>
                  )}
                </div>
              </div>
              <button onClick={() => setFilterModalOpen(true)}
                className={`relative flex-shrink-0 px-3 py-2.5 rounded-lg border transition-colors ${activeFilterCount > 0 ? "border-accent/40 bg-accent/10 text-accent-foreground" : "border-border bg-card text-muted-foreground hover:text-foreground hover:border-muted-foreground/30"}`}
                title="Filters">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
                </svg>
                {activeFilterCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-accent text-[9px] font-bold text-black flex items-center justify-center">{activeFilterCount}</span>
                )}
              </button>
            </div>

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

          {/* AI Digest panel. always visible (except in search) */}
          {!searchActive && (
            <div className="rounded-xl border border-accent/30 bg-gradient-to-br from-accent/10 to-card/50 p-4 mb-3 flex-shrink-0 shadow-sm">
              <div className="flex items-start justify-between gap-3 mb-2">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="w-7 h-7 rounded-full bg-accent/20 border border-accent/40 flex items-center justify-center text-accent flex-shrink-0">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2L9.1 8.6 2 9.3l5.5 4.9L5.8 22 12 18l6.2 4-1.7-7.8L22 9.3l-7.1-.7L12 2z"/>
                    </svg>
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-semibold">AI Digest</span>
                      <span className="text-[10px] text-muted-foreground">
                        {selectedCamera && cameraMap[selectedCamera] ? cameraMap[selectedCamera].name : "All Cameras"}
                      </span>
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      {digestPeriod === "hourly" ? "Summary of the last hour" : "Summary of the last 24 hours"}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <div className="flex rounded-md border border-border overflow-hidden">
                    <button onClick={() => setDigestPeriod("hourly")}
                      className={`px-2 py-1 text-[10px] transition-colors ${digestPeriod === "hourly" ? "bg-accent text-black font-medium" : "text-muted-foreground hover:bg-muted"}`}>
                      1h
                    </button>
                    <button onClick={() => setDigestPeriod("daily")}
                      className={`px-2 py-1 text-[10px] border-l border-border transition-colors ${digestPeriod === "daily" ? "bg-accent text-black font-medium" : "text-muted-foreground hover:bg-muted"}`}>
                      24h
                    </button>
                  </div>
                  <button onClick={fetchDigest} disabled={digestLoading}
                    title="Regenerate digest"
                    className="p-1.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={digestLoading ? "animate-spin" : ""}>
                      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>
                    </svg>
                  </button>
                </div>
              </div>

              {digestLoading && !digest ? (
                <div className="space-y-1.5 animate-pulse">
                  <div className="h-2.5 w-5/6 rounded bg-muted/70" />
                  <div className="h-2.5 w-4/6 rounded bg-muted/70" />
                  <div className="h-2.5 w-3/6 rounded bg-muted/50" />
                </div>
              ) : digest && digest.total_observations > 0 ? (
                <>
                  <p className="text-xs leading-relaxed">{digest.summary}</p>
                  {digest.highlights.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {digest.highlights.slice(0, 4).map((h, i) => (
                        <span key={i} className="px-2 py-0.5 text-[10px] rounded-full bg-background/60 border border-border text-muted-foreground">{h}</span>
                      ))}
                    </div>
                  )}
                  <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                    <span>{digest.total_observations} observation{digest.total_observations > 1 ? "s" : ""} analyzed</span>
                    <span className="font-mono">{digest.period_label}</span>
                  </div>

                  {/* 24h person gallery */}
                  {digestPeriod === "daily" && (
                    <div className="mt-3 pt-3 border-t border-border/50">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">People seen today</span>
                        {personSummariesLoading && <span className="text-[10px] text-muted-foreground">loading.</span>}
                      </div>
                      {(() => {
                        const seen = personSummaries.filter((p) => p.sightings_24h > 0);
                        const unknowns = clusterSummaries.filter((c) => c.sightings_24h > 0);
                        if (seen.length === 0 && unknowns.length === 0 && !personSummariesLoading) {
                          return <p className="text-[11px] text-muted-foreground">No faces recognized or grouped in the last 24 hours.</p>;
                        }
                        return (
                          <div className="flex gap-2 overflow-x-auto scrollbar-thin pb-1">
                            {seen.map((p) => (
                              <button key={p.person_id} onClick={() => setSelectedPersonId(p.person_id)}
                                className="flex-shrink-0 w-20 text-center group">
                                <div className="w-16 h-16 mx-auto rounded-full overflow-hidden border-2 border-border group-hover:border-accent transition-colors bg-muted">
                                  {p.photo_path ? (
                                    <img src={`/api/persons/${p.person_id}/photo`} alt={p.display_name} className="w-full h-full object-cover" />
                                  ) : (
                                    <div className="w-full h-full flex items-center justify-center text-sm font-semibold text-muted-foreground">
                                      {p.display_name.charAt(0).toUpperCase()}
                                    </div>
                                  )}
                                </div>
                                <div className="mt-1 text-[11px] font-medium truncate">{p.display_name}</div>
                                <div className="text-[9px] text-muted-foreground">{p.sightings_24h} visit{p.sightings_24h > 1 ? "s" : ""}</div>
                              </button>
                            ))}
                            {unknowns.map((c) => (
                              <button key={c.cluster_id} onClick={() => setSelectedClusterId(c.cluster_id)}
                                className="flex-shrink-0 w-20 text-center group" title={c.appearance_description || ""}>
                                <div className="w-16 h-16 mx-auto rounded-full overflow-hidden border-2 border-dashed border-amber-500/50 group-hover:border-amber-400 transition-colors bg-muted">
                                  {c.sample_thumbnail_path ? (
                                    <img src={`/api/persons/suggestions/${c.cluster_id}/thumbnail`} alt={c.auto_label} className="w-full h-full object-cover" />
                                  ) : (
                                    <div className="w-full h-full flex items-center justify-center text-xs font-semibold text-amber-400/80">?</div>
                                  )}
                                </div>
                                <div className="mt-1 text-[11px] font-medium truncate text-amber-300/90">{c.auto_label}</div>
                                <div className="text-[9px] text-muted-foreground truncate">
                                  {c.appearance_description || (c.appearance_description_status === "pending" ? "describing." : `${c.sightings_24h} visit${c.sightings_24h > 1 ? "s" : ""}`)}
                                </div>
                              </button>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </>
              ) : (
                <div className="text-xs text-muted-foreground leading-relaxed">
                  {cameras.length === 0
                    ? "Connect a camera to start generating activity summaries."
                    : digestPeriod === "hourly"
                      ? "No activity in the last hour. The digest will appear as soon as events are observed."
                      : "No activity in the last 24 hours. Try adjusting cameras or check back later."}
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
              <div className="flex flex-col items-center justify-center py-20 gap-3">
                <svg className="animate-spin h-5 w-5 text-muted-foreground" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                <div className="text-xs text-muted-foreground">Searching observations.</div>
              </div>
            ) : timelineLoading && entries.length === 0 ? (
              <div className="space-y-3">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="rounded-lg border border-border bg-card/30 p-3 animate-pulse">
                    <div className="flex items-center justify-between mb-2">
                      <div className="h-3 w-32 rounded bg-muted" />
                      <div className="h-3 w-16 rounded bg-muted" />
                    </div>
                    <div className="h-2.5 w-4/5 rounded bg-muted/70 mb-1.5" />
                    <div className="h-2.5 w-2/3 rounded bg-muted/70" />
                  </div>
                ))}
              </div>
            ) : entries.length === 0 ? (
              cameras.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border bg-card/30 p-8 text-center">
                  <div className="w-12 h-12 rounded-full bg-accent/10 border border-accent/30 flex items-center justify-center mx-auto mb-3 text-accent">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                    </svg>
                  </div>
                  <h3 className="text-sm font-semibold mb-1">Connect your first camera</h3>
                  <p className="text-xs text-muted-foreground max-w-sm mx-auto mb-4 leading-relaxed">
                    The timeline fills in as motion, faces, and objects are detected. Add any RTSP feed, discover ONVIF cameras on your network, or use this device as a test source.
                  </p>
                  <div className="flex items-center justify-center gap-2">
                    <button onClick={() => setModalOpen(true)}
                      className="px-3 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90">
                      Add a camera
                    </button>
                    <button onClick={() => setModalOpen(true)}
                      className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted">
                      Use webcam
                    </button>
                  </div>
                </div>
              ) : searchActive ? (
                <div className="rounded-xl border border-dashed border-border bg-card/30 p-8 text-center">
                  <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center mx-auto mb-3 text-muted-foreground">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                    </svg>
                  </div>
                  <h3 className="text-sm font-semibold mb-1">No matches</h3>
                  <p className="text-xs text-muted-foreground max-w-sm mx-auto mb-4">
                    Nothing matched {searchQuery.trim() ? <>&ldquo;<span className="font-medium text-foreground">{searchQuery.trim()}</span>&rdquo;</> : "these filters"}. Try broadening the time range or removing filters.
                  </p>
                  <button onClick={clearAllFilters}
                    className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted">
                    Clear filters
                  </button>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-border bg-card/30 p-8 text-center">
                  <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center mx-auto mb-3 text-muted-foreground">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                    </svg>
                  </div>
                  <h3 className="text-sm font-semibold mb-1">Nothing happened yet</h3>
                  <p className="text-xs text-muted-foreground max-w-sm mx-auto mb-4 leading-relaxed">
                    {cameras.some((c) => c.status === "offline")
                      ? "Some cameras are offline. Check their stream URLs or credentials."
                      : "Cameras are connected and watching. Events will appear here as soon as something moves."}
                  </p>
                  <div className="flex items-center justify-center gap-2 flex-wrap">
                    {activeFilterCount > 0 && (
                      <button onClick={clearAllFilters}
                        className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted">
                        Clear filters ({activeFilterCount})
                      </button>
                    )}
                    <button onClick={() => setTimeRange("30d")}
                      className="px-3 py-1.5 text-xs rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted">
                      Try last 30 days
                    </button>
                  </div>
                </div>
              )
            ) : (
              <div className="space-y-3">
                {hourGroups.map(({ key: bucketKey, entries: dateEntries }) => {
                  const d = searchActive ? null : bucketDigest(dateEntries);
                  const isExpanded = searchActive || expandedBuckets.has(bucketKey);
                  return (
                  <div key={bucketKey}>
                    {!searchActive && d && (
                      <button
                        onClick={() => {
                          setExpandedBuckets((prev) => {
                            const next = new Set(prev);
                            if (next.has(bucketKey)) next.delete(bucketKey); else next.add(bucketKey);
                            return next;
                          });
                        }}
                        className={`w-full text-left rounded-lg border p-3 mb-1.5 transition-colors ${isExpanded ? "border-accent/50 bg-card" : "border-border bg-card/50 hover:border-accent/40 hover:bg-card"}`}
                      >
                        <div className="flex items-start justify-between gap-3 mb-1">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-xs font-semibold">{formatHourBucket(bucketKey)}</span>
                            <span className="text-[10px] text-muted-foreground">{d.total} event{d.total > 1 ? "s" : ""}</span>
                          </div>
                          <span className="text-[10px] text-muted-foreground">{isExpanded ? "\u25BC hide" : "\u25B6 show"}</span>
                        </div>
                        <p className="text-xs text-muted-foreground leading-relaxed">{d.summary}</p>
                        {d.topCams.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1 mt-1.5">
                            {d.topCams.map((c, i) => (
                              <span key={i} className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{c.name} ({c.n})</span>
                            ))}
                          </div>
                        )}
                      </button>
                    )}
                    {searchActive && (
                      <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-2 sticky top-0 bg-background/80 backdrop-blur-sm py-1 z-10">Search Results</div>
                    )}
                    {isExpanded && (
                    <div className="space-y-1.5 pl-2 border-l border-border/50">
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
                                            {srUnknown.length > 0 && <span className="text-xs text-yellow-400">{srUnknown.length === 1 ? "Unknown person" : `${srUnknown.length} unknown`}</span>}
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
                          // Match recording status log to nearest Recording (same cam, within 30s)
                          let matchedRec: Recording | null = null;
                          if (log.status === "recording") {
                            const logTs = new Date(log.timestamp).getTime();
                            let best = Infinity;
                            for (const r of recordings) {
                              if (r.camera_id !== log.camera_id) continue;
                              const d = Math.abs(new Date(r.started_at).getTime() - logTs);
                              if (d < best && d <= 30000) { best = d; matchedRec = r; }
                            }
                          }
                          const row = (
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <div className={`w-1.5 h-1.5 rounded-full ${statusColor(log.status)}`} />
                                <span className="text-xs"><span className="font-medium">{cam?.name || "Unknown"}</span><span className="mx-1 text-muted-foreground">{log.status === "recording" ? "started" : log.status === "offline" ? "went" : "is"}</span><span className={isOnline ? "text-green-400" : "text-muted-foreground"}>{log.status === "recording" ? "recording" : statusLabel(log.status).toLowerCase()}</span></span>
                              </div>
                              <span className="text-[10px] text-muted-foreground font-mono">{formatTime(log.timestamp)}</span>
                            </div>
                          );
                          if (!matchedRec) {
                            return (
                              <div key={entry.id} className="px-3 py-2 rounded-lg border border-border/50">
                                {row}
                              </div>
                            );
                          }
                          const rec = matchedRec;
                          return (
                            <div key={entry.id}>
                              <button onClick={() => setActiveEntry(isActive ? null : entry.id)}
                                className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${isActive ? "border-accent bg-card" : "border-border/50 hover:border-accent/50 hover:bg-card/50"}`}>
                                {row}
                              </button>
                              {isActive && (
                                <div className="mt-1.5 rounded-lg overflow-hidden border border-border bg-black">
                                  <video controls autoPlay className="w-full aspect-video" src={`/api/recordings/${rec.id}/stream`} />
                                </div>
                              )}
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
                          <div key={entry.id} className="rounded-lg border border-border hover:border-accent/50 hover:bg-card/50 overflow-hidden transition-colors">
                            <div className="flex gap-3">
                              {/* Inline thumbnail */}
                              {hasThumb && (
                                <div className="w-24 h-20 flex-shrink-0 bg-black/50 overflow-hidden">
                                  <img src={`/api/observations/${obs.id}/thumbnail`} alt="" className="w-full h-full object-cover" />
                                </div>
                              )}
                              <div className={`flex-1 min-w-0 py-2 ${hasThumb ? "pr-3" : "px-3"}`}>
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0 flex-1">
                                    {/* Person names as headline */}
                                    {hasFaces && (
                                      <div className="flex flex-wrap items-center gap-1 mb-1">
                                        {namedFaces.map((f, i) => (
                                          <span key={`n${i}`} className="text-xs font-medium text-green-400">
                                            {f.person_name}
                                            {f.match_distance != null && <span className="ml-1 text-[10px] text-muted-foreground">{((1 - f.match_distance) * 100).toFixed(0)}%</span>}
                                          </span>
                                        ))}
                                        {namedFaces.length > 0 && unknownFaces.length > 0 && <span className="text-[10px] text-muted-foreground">+</span>}
                                        {unknownFaces.length > 0 && (
                                          <span className="text-xs text-yellow-400">{unknownFaces.length === 1 ? "Unknown person" : `${unknownFaces.length} unknown`}</span>
                                        )}
                                      </div>
                                    )}

                                    {/* VLM description (full, 2-line clamp) */}
                                    {obs.vlm_description ? (
                                      <p className="text-xs leading-relaxed line-clamp-2">{obs.vlm_description}</p>
                                    ) : !hasFaces && (
                                      <p className="text-xs font-medium line-clamp-1">{summarizeDetections(obs)}</p>
                                    )}

                                    {/* Detection tags with confidence */}
                                    <div className="flex flex-wrap items-center gap-1 mt-1">
                                      {objects.slice(0, 6).map((d, i) => (
                                        <span key={i} className="px-1 py-0.5 text-[9px] rounded bg-blue-900/30 text-blue-300 border border-blue-800/40">
                                          {d.label} <span className="text-blue-400/70">{(d.confidence * 100).toFixed(0)}%</span>
                                        </span>
                                      ))}
                                      {objects.length > 6 && <span className="text-[9px] text-muted-foreground">+{objects.length - 6}</span>}
                                      {plates.map((d, i) => (
                                        <span key={`p${i}`} className="px-1 py-0.5 text-[9px] rounded bg-accent/20 text-accent border border-accent/40">{d.plate_text}</span>
                                      ))}
                                      <span className="px-1 py-0.5 text-[9px] rounded bg-muted/50 text-muted-foreground">{cam?.name || "Unknown"}</span>
                                      {obs.vlm_provider && (
                                        <span className="px-1 py-0.5 text-[9px] rounded bg-muted/30 text-muted-foreground font-mono">via {obs.vlm_provider}</span>
                                      )}
                                    </div>
                                  </div>
                                  <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0 pt-0.5">{formatTime(obs.started_at)}</span>
                                </div>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    )}
                  </div>
                  );
                })}
              </div>
            )}
          </div>
        </main>
      </div>

      {modalOpen && <AddCameraModal onClose={() => setModalOpen(false)} onSuccess={() => { setModalOpen(false); fetchCameras(); }} />}
      {selectedPersonId && (
        <PersonActivityModal
          personId={selectedPersonId}
          personName={personSummaries.find((p) => p.person_id === selectedPersonId)?.display_name || "Person"}
          onClose={() => setSelectedPersonId(null)}
        />
      )}
      {selectedClusterId && (() => {
        const c = clusterSummaries.find((x) => x.cluster_id === selectedClusterId);
        const label = c ? (c.appearance_description ? `${c.auto_label}. ${c.appearance_description}` : c.auto_label) : "Unknown";
        return (
          <PersonActivityModal
            personId={selectedClusterId}
            personName={label}
            mode="cluster"
            onClose={() => setSelectedClusterId(null)}
          />
        );
      })()}
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
