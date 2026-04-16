"use client";

import { useState, useEffect, useCallback } from "react";

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

interface Digest {
  period: string;
  period_label: string;
  total_observations: number;
  summary: string;
  highlights: string[];
}

interface Observation {
  id: string;
  camera_id: string;
  started_at: string;
  object_detections: { objects: { label: string; confidence: number }[]; count: number } | null;
  person_detections: { faces: { person_name: string | null; person_id: string | null }[]; count: number } | null;
  vlm_description: string | null;
}

interface ActivityEvent {
  id: string;
  timestamp: string;
  summary: string;
  icon: "person" | "object" | "scene";
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function observationToEvents(obs: Observation): ActivityEvent[] {
  const events: ActivityEvent[] = [];

  // Named people first
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

  // Object detections (grouped by label)
  if (obs.object_detections?.objects && obs.object_detections.objects.length > 0) {
    const counts: Record<string, number> = {};
    for (const obj of obs.object_detections.objects) {
      counts[obj.label] = (counts[obj.label] || 0) + 1;
    }

    // Only show top 2 object types to keep it compact
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 2);
    const parts = sorted.map(([label, count]) => count === 1 ? label : `${count} ${label}s`);

    // Skip if we already have person events from this observation
    if (events.length === 0 && parts.length > 0) {
      events.push({
        id: `${obs.id}-objects`,
        timestamp: obs.started_at,
        summary: parts.join(", ") + " detected",
        icon: "object",
      });
    }
  }

  // VLM scene description as fallback
  if (events.length === 0 && obs.vlm_description) {
    // Truncate to first sentence, max 60 chars
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

const STREAM_TYPES: { value: StreamType; label: string; hint: string; placeholder: string }[] = [
  { value: "rtsp", label: "RTSP", hint: "IP cameras, NVRs, most security cameras", placeholder: "rtsp://192.168.1.100:554/stream1" },
  { value: "http_mjpeg", label: "HTTP MJPEG", hint: "Motion JPEG over HTTP. Webcams, ESP32-CAM", placeholder: "http://192.168.1.100:8080/video" },
  { value: "http_snapshot", label: "HTTP Snapshot", hint: "Periodic JPEG pull. Low-bandwidth cameras", placeholder: "http://192.168.1.100/snapshot.jpg" },
  { value: "hls", label: "HLS", hint: "HTTP Live Streaming. Cloud cameras, Wyze, Ring", placeholder: "http://192.168.1.100/live/stream.m3u8" },
  { value: "usb", label: "USB / Local", hint: "Locally attached USB or CSI cameras", placeholder: "0" },
  { value: "file", label: "File / Test", hint: "Local video file for testing", placeholder: "/path/to/video.mp4" },
];

function extractStreamName(streamUrl: string): string {
  // Extract the last path segment from an RTSP URL
  // e.g. rtsp://host:554/cam/front-door -> front-door
  try {
    const path = streamUrl.replace(/\/+$/, "");
    const lastSlash = path.lastIndexOf("/");
    return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
  } catch {
    return streamUrl;
  }
}

function StatusBadge({ status }: { status: Camera["status"] }) {
  const config = {
    live: { color: "bg-green-500", label: "Live" },
    recording: { color: "bg-danger", label: "Recording" },
    offline: { color: "bg-gray-500", label: "Offline" },
  };
  const { color, label } = config[status];

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={`w-2 h-2 rounded-full ${color} ${status !== "offline" ? "pulse-dot" : ""}`} />
      {label}
    </span>
  );
}

function ActivityTicker({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) return null;

  const iconMap = {
    person: (
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    ),
    object: (
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="2" width="20" height="20" rx="2" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
    scene: (
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
  };

  return (
    <div className="border-t border-border">
      <div className="px-3 py-1.5 flex items-center gap-1.5">
        <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider shrink-0">
          Activity
        </span>
        <div className="flex-1 h-px bg-border" />
      </div>
      <div className="overflow-x-auto scrollbar-thin pb-2 px-3">
        <div className="flex gap-0 min-w-max relative">
          {/* Timeline line */}
          <div className="absolute top-[9px] left-0 right-0 h-px bg-border" />

          {events.map((evt, i) => (
            <div key={evt.id} className="relative flex flex-col items-start min-w-[120px] max-w-[160px] pr-3">
              {/* Timeline dot */}
              <div className={`relative z-10 w-[18px] h-[18px] rounded-full border-2 flex items-center justify-center ${
                evt.icon === "person"
                  ? "border-green-500 bg-green-500/10 text-green-400"
                  : evt.icon === "object"
                    ? "border-blue-400 bg-blue-400/10 text-blue-400"
                    : "border-muted-foreground bg-muted/50 text-muted-foreground"
              }`}>
                {iconMap[evt.icon]}
              </div>
              {/* Content */}
              <div className="mt-1.5 pl-0.5">
                <p className="text-[11px] leading-tight text-foreground/80">
                  {evt.summary}
                </p>
                <span className="text-[10px] text-muted-foreground font-mono mt-0.5 block">
                  {timeAgo(evt.timestamp)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CameraCard({ camera, digest, digestLoading, onRefreshDigest, activityEvents }: {
  camera: Camera;
  digest: Digest | null;
  digestLoading: boolean;
  onRefreshDigest: () => void;
  activityEvents: ActivityEvent[];
}) {
  const streamName = extractStreamName(camera.stream_url);
  const iframeSrc = `${WEBRTC_URL}/${streamName}/`;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden group">
      <div
        onClick={() => (window.location.href = `/timeline?camera=${camera.id}`)}
        className="cursor-pointer hover:bg-card-elevated/30 transition-colors"
      >
        <div className="relative aspect-video bg-black">
          {camera.status !== "offline" ? (
            <iframe
              src={iframeSrc}
              className="absolute inset-0 w-full h-full border-0"
              allow="autoplay; encrypted-media"
              sandbox="allow-scripts allow-same-origin"
            />
          ) : (
            <div className="camera-feed absolute inset-0 flex items-center justify-center">
              <div className="scanline absolute inset-0" />
              <span className="text-xs text-muted-foreground font-mono z-10">
                OFFLINE
              </span>
            </div>
          )}

          {/* Settings gear */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              window.location.href = `/cameras/${camera.id}`;
            }}
            className="absolute top-2 right-2 z-10 w-7 h-7 rounded-md bg-black/60 backdrop-blur-sm border border-white/10 flex items-center justify-center text-white/70 hover:text-white hover:bg-black/80 transition-colors opacity-0 group-hover:opacity-100"
            title="Camera settings"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
        </div>

        <div className="px-3 py-2.5 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{camera.name}</div>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wide px-1 py-0.5 rounded bg-muted/50">
                {camera.stream_type?.replace("_", " ") || "rtsp"}
              </span>
              {camera.location_label && (
                <span className="text-xs text-muted-foreground truncate">
                  {camera.location_label}
                </span>
              )}
            </div>
          </div>
          <StatusBadge status={camera.status} />
        </div>

        {(camera.width || camera.fps) && (
          <div className="px-3 pb-2 flex gap-3">
            {camera.width && camera.height && (
              <span className="font-mono text-[11px] text-muted-foreground">
                {camera.width}x{camera.height}
              </span>
            )}
            {camera.fps && (
              <span className="font-mono text-[11px] text-muted-foreground">
                {camera.fps}fps
              </span>
            )}
          </div>
        )}
      </div>

      {/* Activity ticker */}
      <ActivityTicker events={activityEvents} />

      {/* Activity digest. Hidden when no observations in period */}
      {(camera.digest_enabled ?? true) && digest && digest.total_observations > 0 && (
        <div className="border-t border-border px-3 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
              Activity Digest
            </span>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-muted-foreground font-mono">
                {camera.digest_period ?? "24h"}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onRefreshDigest(); }}
                disabled={digestLoading}
                className="text-[10px] text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
              >
                {digestLoading ? "..." : "↻"}
              </button>
            </div>
          </div>
          <p className="text-xs leading-relaxed text-foreground/80">
            {digest.summary}
          </p>
          {digest.highlights.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {digest.highlights.slice(0, 2).map((h, i) => (
                <div key={i} className="text-[11px] text-muted-foreground">
                  {h}
                </div>
              ))}
            </div>
          )}
          <div className="text-[10px] text-muted-foreground font-mono mt-1">
            {digest.total_observations} observations · {digest.period_label}
          </div>
        </div>
      )}
    </div>
  );
}

function AddCameraModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
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

  const selectedType = STREAM_TYPES.find((t) => t.value === streamType)!;
  const supportsAuth = ["rtsp", "http_mjpeg", "http_snapshot", "hls"].includes(streamType);
  const supportsSnapshotInterval = streamType === "http_snapshot";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !streamUrl.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
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
      if (supportsAuth && authToken.trim()) {
        payload.auth_token = authToken.trim();
      }
      if (supportsSnapshotInterval) {
        payload.snapshot_interval = snapshotInterval;
      }

      const res = await fetch(`/api/cameras`, {
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

  const inputClass = "w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-lg mx-4 rounded-lg border border-border bg-card-elevated p-6 shadow-xl max-h-[90vh] overflow-y-auto scrollbar-thin">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">Add Camera</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition-colors text-xl leading-none"
          >
            &times;
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Camera name */}
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Front Door"
              required
              className={inputClass}
            />
          </div>

          {/* Stream type selector */}
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Feed Type
            </label>
            <div className="grid grid-cols-3 gap-1.5">
              {STREAM_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => {
                    setStreamType(t.value);
                    setStreamUrl("");
                  }}
                  className={`px-2 py-2 text-xs rounded-md border transition-colors text-center ${
                    streamType === t.value
                      ? "border-accent bg-accent/10 text-accent-foreground"
                      : "border-border hover:border-muted-foreground text-muted-foreground"
                  }`}
                >
                  <div className="font-medium">{t.label}</div>
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5">
              {selectedType.hint}
            </p>
          </div>

          {/* Stream URL / source */}
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              {streamType === "usb" ? "Device Index or Path" : streamType === "file" ? "File Path" : "Stream URL"}
            </label>
            <input
              type="text"
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              placeholder={selectedType.placeholder}
              required
              className={`${inputClass} font-mono text-xs`}
            />
            {streamType === "usb" && (
              <p className="text-[11px] text-muted-foreground mt-1">
                Use 0 for first USB camera, 1 for second, or /dev/video0 on Linux
              </p>
            )}
          </div>

          {/* Snapshot interval */}
          {supportsSnapshotInterval && (
            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">
                Poll Interval
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={0.5}
                  max={30}
                  step={0.5}
                  value={snapshotInterval}
                  onChange={(e) => setSnapshotInterval(Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                  {snapshotInterval}s
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground mt-1">
                How often to fetch a new frame. Lower = more bandwidth
              </p>
            </div>
          )}

          {/* Location */}
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Location Label
            </label>
            <input
              type="text"
              value={locationLabel}
              onChange={(e) => setLocationLabel(e.target.value)}
              placeholder="Optional"
              className={inputClass}
            />
          </div>

          {/* Auth section */}
          {supportsAuth && (
            <div>
              <button
                type="button"
                onClick={() => setShowAuth(!showAuth)}
                className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                <span className={`text-xs transition-transform ${showAuth ? "rotate-90" : ""}`}>▶</span>
                Authentication
                <span className="text-[11px] text-muted-foreground">(optional)</span>
              </button>

              {showAuth && (
                <div className="mt-3 space-y-3 pl-4 border-l border-border-subtle">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] text-muted-foreground mb-1">
                        Username
                      </label>
                      <input
                        type="text"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        placeholder="admin"
                        className={`${inputClass} text-xs`}
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted-foreground mb-1">
                        Password
                      </label>
                      <input
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="••••••••"
                        className={`${inputClass} text-xs`}
                      />
                    </div>
                  </div>

                  <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="flex-1 h-px bg-border" />
                    or
                    <span className="flex-1 h-px bg-border" />
                  </div>

                  <div>
                    <label className="block text-[11px] text-muted-foreground mb-1">
                      Bearer Token / API Key
                    </label>
                    <input
                      type="password"
                      value={authToken}
                      onChange={(e) => setAuthToken(e.target.value)}
                      placeholder="Token for API-based cameras"
                      className={`${inputClass} text-xs font-mono`}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <p className="text-sm text-danger">{error}</p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !name.trim() || !streamUrl.trim()}
              className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Adding..." : "Add Camera"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [digests, setDigests] = useState<Record<string, Digest>>({});
  const [digestLoading, setDigestLoading] = useState<Record<string, boolean>>({});
  const [activityEvents, setActivityEvents] = useState<Record<string, ActivityEvent[]>>({});

  const fetchCameras = useCallback(async () => {
    try {
      const res = await fetch(`/api/cameras`);
      if (res.ok) {
        const data = await res.json();
        setCameras(data);
      }
    } catch {
      // Silently handle fetch errors. The UI shows the current state.
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchActivity = useCallback(async (cameraId: string) => {
    try {
      const res = await fetch(`/api/observations?camera_id=${cameraId}&limit=15`);
      if (res.ok) {
        const observations: Observation[] = await res.json();
        const events = observations.flatMap(observationToEvents).slice(0, 10);
        setActivityEvents((prev) => ({ ...prev, [cameraId]: events }));
      }
    } catch {
      /* silent */
    }
  }, []);

  const fetchDigest = useCallback(async (cam: Camera) => {
    if (!(cam.digest_enabled ?? true)) return;
    const period = cam.digest_period ?? "24h";
    setDigestLoading((prev) => ({ ...prev, [cam.id]: true }));
    try {
      const res = await fetch(`/api/search/digest?period=${period}&camera_id=${cam.id}`);
      if (res.ok) {
        const data = await res.json();
        setDigests((prev) => ({ ...prev, [cam.id]: data }));
      }
    } catch {
      /* silent */
    } finally {
      setDigestLoading((prev) => ({ ...prev, [cam.id]: false }));
    }
  }, []);

  // Fetch on mount
  useEffect(() => {
    fetchCameras();
  }, [fetchCameras]);

  // Auto-refresh cameras every 10 seconds
  useEffect(() => {
    const interval = setInterval(fetchCameras, 10_000);
    return () => clearInterval(interval);
  }, [fetchCameras]);

  // Auto-fetch activity and digests when cameras load
  useEffect(() => {
    if (cameras.length > 0) {
      cameras.forEach((cam) => {
        if (!activityEvents[cam.id]) {
          fetchActivity(cam.id);
        }
        if (!digests[cam.id] && !digestLoading[cam.id]) {
          fetchDigest(cam);
        }
      });
    }
  }, [cameras]); // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh activity every 15 seconds
  useEffect(() => {
    if (cameras.length === 0) return;
    const interval = setInterval(() => {
      cameras.forEach((cam) => fetchActivity(cam.id));
    }, 15_000);
    return () => clearInterval(interval);
  }, [cameras, fetchActivity]);

  function handleAddSuccess() {
    setModalOpen(false);
    fetchCameras();
  }

  const cameraCount = cameras.length;

  return (
    <div className="px-6 py-6">
      {/* Header */}
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cameras</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {loading
              ? "Loading cameras..."
              : cameraCount === 0
                ? "No cameras configured yet"
                : `${cameraCount} camera${cameraCount !== 1 ? "s" : ""} connected`}
          </p>
        </div>
        <div className="flex gap-2">
          <button className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">
            Grid view
          </button>
          <button
            onClick={() => setModalOpen(true)}
            className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity"
          >
            + Add camera
          </button>
        </div>
      </div>

      {/* Camera grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {cameras.map((camera) => (
          <CameraCard
            key={camera.id}
            camera={camera}
            digest={digests[camera.id] || null}
            digestLoading={digestLoading[camera.id] || false}
            onRefreshDigest={() => fetchDigest(camera)}
            activityEvents={activityEvents[camera.id] || []}
          />
        ))}

        {/* Dashed add-camera tile (always visible when empty, or as the last tile) */}
        {cameraCount === 0 && !loading && (
          <div
            onClick={() => setModalOpen(true)}
            className="rounded-lg border border-dashed border-border bg-transparent hover:border-accent transition-colors cursor-pointer flex items-center justify-center aspect-video"
          >
            <div className="text-center">
              <div className="w-10 h-10 rounded-full border border-border flex items-center justify-center mx-auto mb-2 text-muted-foreground">
                +
              </div>
              <div className="text-sm text-muted-foreground">Add camera</div>
              <div className="font-mono text-[11px] text-muted-foreground mt-1">
                ONVIF discover or RTSP url
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Add camera modal */}
      {modalOpen && (
        <AddCameraModal
          onClose={() => setModalOpen(false)}
          onSuccess={handleAddSuccess}
        />
      )}
    </div>
  );
}
