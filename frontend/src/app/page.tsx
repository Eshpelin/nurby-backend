"use client";

import { useState, useEffect, useCallback } from "react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WEBRTC_URL =
  process.env.NEXT_PUBLIC_WEBRTC_URL || "http://localhost:8889";

interface Camera {
  id: string;
  name: string;
  stream_url: string;
  location_label: string | null;
  status: "offline" | "live" | "recording";
  width: number | null;
  height: number | null;
  fps: number | null;
  recording_enabled: boolean;
  created_at: string;
  updated_at: string;
}

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

function CameraCard({ camera }: { camera: Camera }) {
  const streamName = extractStreamName(camera.stream_url);
  const iframeSrc = `${WEBRTC_URL}/${streamName}/`;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden group">
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
      </div>

      <div className="px-3 py-2.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{camera.name}</div>
          {camera.location_label && (
            <div className="text-xs text-muted-foreground truncate mt-0.5">
              {camera.location_label}
            </div>
          )}
        </div>
        <StatusBadge status={camera.status} />
      </div>

      {(camera.width || camera.fps) && (
        <div className="px-3 pb-2.5 flex gap-3">
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
  const [streamUrl, setStreamUrl] = useState("");
  const [locationLabel, setLocationLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !streamUrl.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch(`${API_URL}/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          stream_url: streamUrl.trim(),
          location_label: locationLabel.trim() || null,
        }),
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md mx-4 rounded-lg border border-border bg-card-elevated p-6 shadow-xl">
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
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Stream URL
            </label>
            <input
              type="text"
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              placeholder="rtsp://192.168.1.100:554/stream"
              required
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent font-mono text-xs"
            />
          </div>

          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Location Label
            </label>
            <input
              type="text"
              value={locationLabel}
              onChange={(e) => setLocationLabel(e.target.value)}
              placeholder="Optional"
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

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

  const fetchCameras = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/cameras`);
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

  // Fetch on mount
  useEffect(() => {
    fetchCameras();
  }, [fetchCameras]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(fetchCameras, 10_000);
    return () => clearInterval(interval);
  }, [fetchCameras]);

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
          <CameraCard key={camera.id} camera={camera} />
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
