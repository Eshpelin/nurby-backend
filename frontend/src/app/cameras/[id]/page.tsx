"use client";

import { useAuth } from "@/lib/auth";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

interface Camera {
  id: string;
  name: string;
  stream_url: string;
  stream_type: string;
  snapshot_url: string | null;
  location_label: string | null;
  username: string | null;
  auth_token: string | null;
  snapshot_interval: number;
  motion_sensitivity: number;
  recording_enabled: boolean;
  recording_mode: string;
  recording_trigger_objects: string[] | null;
  recording_clip_pre: number;
  recording_clip_post: number;
  vlm_provider_id: string | null;
  vlm_prompt: string | null;
  vlm_interval: number;
  vlm_max_tokens: number;
  detect_objects: boolean;
  detect_faces: boolean;
  scene_mode: string;
  object_confidence: number;
  vlm_trigger: string;
  vlm_trigger_objects: string[] | null;
  digest_enabled: boolean;
  digest_period: string;
  digest_provider_id: string | null;
  digest_prompt: string | null;
  retention_mode: string;
  retention_days: number;
  retention_gb: number;
  detection_models: {model: string; confidence: number; enabled: boolean; label_filter: string[]}[] | null;
  detection_merge: string;
  detection_consensus_min: number;
  motion_zones: MotionZone[] | null;
  status: string;
  width: number | null;
  height: number | null;
  fps: number | null;
  created_at: string;
  updated_at: string;
}

interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  default_model: string | null;
  active: boolean;
}

const STREAM_TYPES: Record<string, string> = {
  rtsp: "RTSP",
  http_mjpeg: "HTTP MJPEG",
  http_snapshot: "HTTP Snapshot",
  hls: "HLS",
  usb: "USB / Local",
  file: "File / Test",
};

const DEFAULT_VLM_PROMPT =
  "You are a security camera AI assistant. Describe what you see in this camera frame in 1-2 concise sentences. Focus on people, vehicles, animals, and any unusual activity. Be specific about locations, actions, and counts. If nothing notable is happening, say so briefly.";

function StatusDot({ status }: { status: string }) {
  const color =
    status === "recording"
      ? "bg-danger"
      : status === "live"
        ? "bg-green-500"
        : "bg-gray-500";
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${color} ${status !== "offline" ? "pulse-dot" : ""}`}
    />
  );
}

function Section({
  title,
  children,
  description,
}: {
  title: string;
  children: React.ReactNode;
  description?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h3 className="text-sm font-semibold mb-1">{title}</h3>
      {description && (
        <p className="text-xs text-muted-foreground mb-4">{description}</p>
      )}
      {!description && <div className="mb-4" />}
      <div className="space-y-4">{children}</div>
    </div>
  );
}

function FieldRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[180px_1fr] gap-4 items-start">
      <div>
        <label className="text-sm text-foreground">{label}</label>
        {hint && <p className="text-[11px] text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        checked ? "bg-accent" : "bg-muted"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
          checked ? "translate-x-[18px]" : "translate-x-[3px]"
        }`}
      />
      {label && (
        <span className="ml-11 text-sm text-muted-foreground whitespace-nowrap">
          {label}
        </span>
      )}
    </button>
  );
}

const inputClass =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent";

interface PTZPreset {
  token: string;
  name: string;
}

interface MotionZone {
  name: string;
  points: number[][];
  type: "include" | "exclude";
}

function HoldButton({
  onHold,
  onRelease,
  children,
  className,
}: {
  onHold: () => void;
  onRelease: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onMouseDown={onHold}
      onMouseUp={onRelease}
      onMouseLeave={onRelease}
      onTouchStart={onHold}
      onTouchEnd={onRelease}
      className={className}
    >
      {children}
    </button>
  );
}

function PTZControlPanel({ cameraId }: { cameraId: string }) {
  const { authFetch } = useAuth();
  const [presets, setPresets] = useState<PTZPreset[]>([]);
  const [speed, setSpeed] = useState(0.5);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchPresets = useCallback(async () => {
    try {
      const res = await authFetch(`/api/cameras/${cameraId}/ptz/presets`);
      if (res.ok) setPresets(await res.json());
    } catch {
      /* silent */
    }
  }, [cameraId]);

  useEffect(() => {
    fetchPresets();
  }, [fetchPresets]);

  const sendMove = useCallback(
    async (pan: number, tilt: number, zoom: number) => {
      try {
        await authFetch(`/api/cameras/${cameraId}/ptz/move`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pan, tilt, zoom, speed }),
        });
      } catch {
        /* silent */
      }
    },
    [cameraId, speed]
  );

  const sendStop = useCallback(async () => {
    try {
      await authFetch(`/api/cameras/${cameraId}/ptz/stop`, { method: "POST" });
    } catch {
      /* silent */
    }
  }, [cameraId]);

  const startHold = useCallback(
    (pan: number, tilt: number, zoom: number) => {
      sendMove(pan, tilt, zoom);
      intervalRef.current = setInterval(() => sendMove(pan, tilt, zoom), 200);
    },
    [sendMove]
  );

  const stopHold = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    sendStop();
  }, [sendStop]);

  const goToPreset = useCallback(
    async (token: string) => {
      try {
        await authFetch(`/api/cameras/${cameraId}/ptz/goto`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preset_token: token }),
        });
      } catch {
        /* silent */
      }
    },
    [cameraId]
  );

  const btnClass =
    "w-10 h-10 flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted transition-colors text-sm font-medium";

  return (
    <div className="space-y-4">
      {/* Directional pad */}
      <div className="flex flex-col items-center gap-1">
        <HoldButton onHold={() => startHold(0, 1, 0)} onRelease={stopHold} className={btnClass}>
          ↑
        </HoldButton>
        <div className="flex gap-1">
          <HoldButton onHold={() => startHold(-1, 0, 0)} onRelease={stopHold} className={btnClass}>
            ←
          </HoldButton>
          <button
            type="button"
            onClick={() => sendStop()}
            className={`${btnClass} text-muted-foreground`}
          >
            ●
          </button>
          <HoldButton onHold={() => startHold(1, 0, 0)} onRelease={stopHold} className={btnClass}>
            →
          </HoldButton>
        </div>
        <HoldButton onHold={() => startHold(0, -1, 0)} onRelease={stopHold} className={btnClass}>
          ↓
        </HoldButton>
      </div>

      {/* Zoom */}
      <div className="flex items-center gap-2 justify-center">
        <HoldButton onHold={() => startHold(0, 0, -1)} onRelease={stopHold} className={btnClass}>
          −
        </HoldButton>
        <span className="text-xs text-muted-foreground">Zoom</span>
        <HoldButton onHold={() => startHold(0, 0, 1)} onRelease={stopHold} className={btnClass}>
          +
        </HoldButton>
      </div>

      {/* Speed */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground">Speed</span>
        <input
          type="range"
          min={0.1}
          max={1}
          step={0.1}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="flex-1 accent-accent"
        />
        <span className="font-mono text-xs text-muted-foreground w-8 text-right">
          {(speed * 100).toFixed(0)}%
        </span>
      </div>

      {/* Presets */}
      {presets.length > 0 && (
        <div>
          <div className="text-xs text-muted-foreground mb-2">Presets</div>
          <div className="flex flex-wrap gap-1">
            {presets.map((p) => (
              <button
                key={p.token}
                type="button"
                onClick={() => goToPreset(p.token)}
                className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
              >
                {p.name || p.token}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ZoneEditorCanvas({
  zones,
  onChange,
  width,
  height,
}: {
  zones: MotionZone[];
  onChange: (zones: MotionZone[]) => void;
  width: number;
  height: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [drawing, setDrawing] = useState(false);
  const [currentPoints, setCurrentPoints] = useState<number[][]>([]);
  const [zoneType, setZoneType] = useState<"include" | "exclude">("include");

  const canvasWidth = 480;
  const canvasHeight = Math.round((canvasWidth * height) / width) || 270;
  const scaleX = canvasWidth / width;
  const scaleY = canvasHeight / height;

  const drawZones = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvasWidth, canvasHeight);

    // Draw existing zones
    zones.forEach((zone) => {
      if (zone.points.length < 2) return;
      ctx.beginPath();
      ctx.moveTo(zone.points[0][0] * scaleX, zone.points[0][1] * scaleY);
      zone.points.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p[0] * scaleX, p[1] * scaleY);
      });
      ctx.closePath();
      ctx.fillStyle = zone.type === "include" ? "rgba(34, 197, 94, 0.2)" : "rgba(239, 68, 68, 0.2)";
      ctx.fill();
      ctx.strokeStyle = zone.type === "include" ? "#22c55e" : "#ef4444";
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      const cx = zone.points.reduce((s, p) => s + p[0] * scaleX, 0) / zone.points.length;
      const cy = zone.points.reduce((s, p) => s + p[1] * scaleY, 0) / zone.points.length;
      ctx.fillStyle = "#fff";
      ctx.font = "11px monospace";
      ctx.textAlign = "center";
      ctx.fillText(zone.name, cx, cy);
    });

    // Draw current drawing
    if (currentPoints.length > 0) {
      ctx.beginPath();
      ctx.moveTo(currentPoints[0][0], currentPoints[0][1]);
      currentPoints.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p[0], p[1]);
      });
      ctx.strokeStyle = zoneType === "include" ? "#22c55e" : "#ef4444";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw points
      currentPoints.forEach((p) => {
        ctx.beginPath();
        ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
        ctx.fillStyle = zoneType === "include" ? "#22c55e" : "#ef4444";
        ctx.fill();
      });
    }
  }, [zones, currentPoints, scaleX, scaleY, canvasWidth, canvasHeight, zoneType]);

  useEffect(() => {
    drawZones();
  }, [drawZones]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (!drawing) {
      setDrawing(true);
      setCurrentPoints([[x, y]]);
    } else {
      setCurrentPoints((prev) => [...prev, [x, y]]);
    }
  };

  const finishZone = () => {
    if (currentPoints.length < 3) return;

    const scaledPoints = currentPoints.map((p) => [
      Math.round(p[0] / scaleX),
      Math.round(p[1] / scaleY),
    ]);

    const newZone: MotionZone = {
      name: `Zone ${zones.length + 1}`,
      points: scaledPoints,
      type: zoneType,
    };

    onChange([...zones, newZone]);
    setDrawing(false);
    setCurrentPoints([]);
  };

  const removeZone = (index: number) => {
    onChange(zones.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-2 mb-2">
        <button
          type="button"
          onClick={() => setZoneType("include")}
          className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
            zoneType === "include"
              ? "border-green-500 bg-green-500/10 text-green-400"
              : "border-border text-muted-foreground"
          }`}
        >
          Include Zone
        </button>
        <button
          type="button"
          onClick={() => setZoneType("exclude")}
          className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
            zoneType === "exclude"
              ? "border-red-500 bg-red-500/10 text-red-400"
              : "border-border text-muted-foreground"
          }`}
        >
          Exclude Zone
        </button>
        {drawing && (
          <button
            type="button"
            onClick={finishZone}
            disabled={currentPoints.length < 3}
            className="px-2.5 py-1.5 text-xs rounded-md border border-accent bg-accent/10 text-accent-foreground disabled:opacity-50"
          >
            Finish Zone ({currentPoints.length} points)
          </button>
        )}
      </div>

      <canvas
        ref={canvasRef}
        width={canvasWidth}
        height={canvasHeight}
        onClick={handleCanvasClick}
        className="border border-border rounded-md cursor-crosshair bg-black/20"
      />

      <p className="text-[11px] text-muted-foreground">
        Click to add points. Click Finish Zone when done (minimum 3 points).
      </p>

      {zones.length > 0 && (
        <div className="space-y-1">
          {zones.map((zone, i) => (
            <div key={i} className="flex items-center justify-between text-xs px-2 py-1.5 rounded border border-border">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    zone.type === "include" ? "bg-green-500" : "bg-red-500"
                  }`}
                />
                <span>{zone.name}</span>
                <span className="text-muted-foreground">
                  ({zone.type}, {zone.points.length} points)
                </span>
              </div>
              <button
                type="button"
                onClick={() => removeZone(i)}
                className="text-muted-foreground hover:text-red-400 transition-colors"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function CameraConfigPage() {
  const { authFetch } = useAuth();
  const params = useParams();
  const router = useRouter();
  const cameraId = params.id as string;

  const [camera, setCamera] = useState<Camera | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  // Form state
  const [name, setName] = useState("");
  const [streamUrl, setStreamUrl] = useState("");
  const [streamType, setStreamType] = useState("rtsp");
  const [locationLabel, setLocationLabel] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [snapshotInterval, setSnapshotInterval] = useState(2);
  const [motionSensitivity, setMotionSensitivity] = useState(0.5);
  const [recordingEnabled, setRecordingEnabled] = useState(true);
  const [recordingMode, setRecordingMode] = useState("always");
  const [recordingTriggerObjects, setRecordingTriggerObjects] = useState<string[]>([]);
  const [recordingClipPre, setRecordingClipPre] = useState(5);
  const [recordingClipPost, setRecordingClipPost] = useState(10);
  const [vlmProviderId, setVlmProviderId] = useState<string | null>(null);
  const [vlmPrompt, setVlmPrompt] = useState("");
  const [vlmInterval, setVlmInterval] = useState(0);
  const [vlmMaxTokens, setVlmMaxTokens] = useState(200);
  const [vlmTrigger, setVlmTrigger] = useState("always");
  const [vlmTriggerObjects, setVlmTriggerObjects] = useState<string[]>([]);
  const [detectObjects, setDetectObjects] = useState(true);
  const [detectFaces, setDetectFaces] = useState(true);
  const [sceneMode, setSceneMode] = useState("indoor");
  const [objectConfidence, setObjectConfidence] = useState(0.35);
  const [detectionModels, setDetectionModels] = useState<{model: string; confidence: number; enabled: boolean; label_filter: string[]}[]>([]);
  const [detectionMerge, setDetectionMerge] = useState("any");
  const [detectionConsensusMin, setDetectionConsensusMin] = useState(2);
  const [digestEnabled, setDigestEnabled] = useState(true);
  const [digestPeriod, setDigestPeriod] = useState("24h");
  const [digestProviderId, setDigestProviderId] = useState<string | null>(null);
  const [digestPrompt, setDigestPrompt] = useState("");
  const [retentionMode, setRetentionMode] = useState("none");
  const [retentionDays, setRetentionDays] = useState(30);
  const [retentionGb, setRetentionGb] = useState(50);
  const [motionZones, setMotionZones] = useState<MotionZone[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const [camRes, provRes] = await Promise.all([
        authFetch(`/api/cameras/${cameraId}`),
        authFetch(`/api/providers`),
      ]);
      if (!camRes.ok) {
        setError("Camera not found");
        setLoading(false);
        return;
      }
      const cam: Camera = await camRes.json();
      const provs: Provider[] = provRes.ok ? await provRes.json() : [];

      setCamera(cam);
      setProviders(provs);

      // Populate form
      setName(cam.name);
      setStreamUrl(cam.stream_url);
      setStreamType(cam.stream_type);
      setLocationLabel(cam.location_label || "");
      setUsername(cam.username || "");
      setPassword("");
      setAuthToken(cam.auth_token || "");
      setSnapshotInterval(cam.snapshot_interval ?? 2);
      setMotionSensitivity(cam.motion_sensitivity ?? 0.5);
      setRecordingEnabled(cam.recording_enabled ?? true);
      setRecordingMode(cam.recording_mode ?? "always");
      setRecordingTriggerObjects(cam.recording_trigger_objects ?? []);
      setRecordingClipPre(cam.recording_clip_pre ?? 5);
      setRecordingClipPost(cam.recording_clip_post ?? 10);
      setVlmProviderId(cam.vlm_provider_id ?? null);
      setVlmPrompt(cam.vlm_prompt || "");
      setVlmInterval(cam.vlm_interval ?? 0);
      setVlmMaxTokens(cam.vlm_max_tokens ?? 200);
      setVlmTrigger(cam.vlm_trigger ?? "always");
      setVlmTriggerObjects(cam.vlm_trigger_objects ?? []);
      setDetectObjects(cam.detect_objects ?? true);
      setDetectFaces(cam.detect_faces ?? true);
      setSceneMode(cam.scene_mode ?? "indoor");
      setObjectConfidence(cam.object_confidence ?? 0.35);
      setDetectionModels(cam.detection_models ?? []);
      setDetectionMerge(cam.detection_merge ?? "any");
      setDetectionConsensusMin(cam.detection_consensus_min ?? 2);
      setDigestEnabled(cam.digest_enabled ?? true);
      setDigestPeriod(cam.digest_period ?? "24h");
      setDigestProviderId(cam.digest_provider_id ?? null);
      setDigestPrompt(cam.digest_prompt || "");
      setRetentionMode(cam.retention_mode ?? "none");
      setRetentionDays(cam.retention_days ?? 30);
      setRetentionGb(cam.retention_gb ?? 50);
      setMotionZones(cam.motion_zones ?? []);
    } catch {
      setError("Failed to load camera");
    } finally {
      setLoading(false);
    }
  }, [cameraId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);

    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        stream_url: streamUrl.trim(),
        stream_type: streamType,
        location_label: locationLabel.trim() || null,
        snapshot_interval: snapshotInterval,
        motion_sensitivity: motionSensitivity,
        recording_enabled: recordingEnabled,
        recording_mode: recordingMode,
        recording_trigger_objects: recordingTriggerObjects.length > 0 ? recordingTriggerObjects : null,
        recording_clip_pre: recordingClipPre,
        recording_clip_post: recordingClipPost,
        vlm_provider_id: vlmProviderId,
        vlm_prompt: vlmPrompt.trim() || null,
        vlm_interval: vlmInterval,
        vlm_max_tokens: vlmMaxTokens,
        vlm_trigger: vlmTrigger,
        vlm_trigger_objects: vlmTriggerObjects.length > 0 ? vlmTriggerObjects : null,
        detect_objects: detectObjects,
        detect_faces: detectFaces,
        scene_mode: sceneMode,
        object_confidence: objectConfidence,
        detection_models: detectionModels.length > 0 ? detectionModels : null,
        detection_merge: detectionMerge,
        detection_consensus_min: detectionConsensusMin,
        digest_enabled: digestEnabled,
        digest_period: digestPeriod,
        digest_provider_id: digestProviderId,
        digest_prompt: digestPrompt.trim() || null,
        retention_mode: retentionMode,
        retention_days: retentionDays,
        retention_gb: retentionGb,
        motion_zones: motionZones.length > 0 ? motionZones : null,
      };

      if (username.trim()) payload.username = username.trim();
      if (password) payload.password = password;
      if (authToken.trim()) payload.auth_token = authToken.trim();

      const res = await authFetch(`/api/cameras/${cameraId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Save failed with status ${res.status}`);
      }

      const updated = await res.json();
      setCamera(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    try {
      const res = await authFetch(`/api/cameras/${cameraId}`, { method: "DELETE" });
      if (res.ok) {
        router.push("/");
      }
    } catch {
      setError("Failed to delete camera");
    }
  }

  function formatInterval(seconds: number): string {
    if (seconds === 0) return "Every keyframe";
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60 ? `${seconds % 60}s` : ""}`.trim();
    return `${Math.floor(seconds / 3600)}h`;
  }

  if (loading) {
    return (
      <div className="px-6 py-6">
        <div className="text-sm text-muted-foreground">Loading camera config...</div>
      </div>
    );
  }

  if (error && !camera) {
    return (
      <div className="px-6 py-6">
        <div className="text-sm text-danger">{error}</div>
        <Link href="/" className="text-sm text-accent hover:underline mt-2 inline-block">
          Back to cameras
        </Link>
      </div>
    );
  }

  if (!camera) return null;

  const supportsAuth = ["rtsp", "http_mjpeg", "http_snapshot", "hls"].includes(streamType);
  const activeProvider = providers.find((p) => p.active);
  const selectedProvider = vlmProviderId
    ? providers.find((p) => p.id === vlmProviderId)
    : null;

  return (
    <div className="px-6 py-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link
          href="/"
          className="text-muted-foreground hover:text-foreground transition-colors text-sm"
        >
          ← Cameras
        </Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="text-lg font-semibold">{camera.name}</h1>
        <StatusDot status={camera.status} />
        <span className="text-xs text-muted-foreground capitalize">{camera.status}</span>
      </div>

      {/* Resolution + FPS info bar */}
      {(camera.width || camera.fps) && (
        <div className="flex gap-4 mb-6 text-xs text-muted-foreground font-mono">
          {camera.width && camera.height && (
            <span>{camera.width}x{camera.height}</span>
          )}
          {camera.fps && <span>{camera.fps} fps</span>}
          <span className="uppercase">{STREAM_TYPES[camera.stream_type] || camera.stream_type}</span>
        </div>
      )}

      <div className="space-y-5">
        {/* ── General ── */}
        <Section title="General" description="Basic camera identification and location">
          <FieldRow label="Name">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputClass}
            />
          </FieldRow>

          <FieldRow label="Location Label" hint="Where this camera is">
            <input
              type="text"
              value={locationLabel}
              onChange={(e) => setLocationLabel(e.target.value)}
              placeholder="e.g. Front porch"
              className={inputClass}
            />
          </FieldRow>
        </Section>

        {/* ── Feed ── */}
        <Section title="Feed" description="Stream source and connection settings">
          <FieldRow label="Feed Type">
            <select
              value={streamType}
              onChange={(e) => setStreamType(e.target.value)}
              className={inputClass}
            >
              {Object.entries(STREAM_TYPES).map(([val, label]) => (
                <option key={val} value={val}>
                  {label}
                </option>
              ))}
            </select>
          </FieldRow>

          <FieldRow
            label={streamType === "usb" ? "Device" : "Stream URL"}
            hint={streamType === "usb" ? "Device index (0, 1) or path" : undefined}
          >
            <input
              type="text"
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              className={`${inputClass} font-mono text-xs`}
            />
          </FieldRow>

          {streamType === "http_snapshot" && (
            <FieldRow label="Poll Interval" hint="Seconds between snapshot fetches">
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
            </FieldRow>
          )}

          <FieldRow label="Recording Mode" hint="When to save video to disk">
            <div className="flex flex-wrap gap-1.5 mb-2">
              {([
                { value: "off", label: "Off" },
                { value: "always", label: "Always" },
                { value: "on_motion", label: "On Motion" },
                { value: "on_object", label: "On Detection" },
                { value: "clip", label: "Clips" },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => {
                    setRecordingMode(opt.value);
                    setRecordingEnabled(opt.value !== "off");
                  }}
                  className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
                    recordingMode === opt.value
                      ? "border-accent bg-accent/10 text-accent-foreground"
                      : "border-border hover:border-muted-foreground text-muted-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {recordingMode === "off"
                ? "No video saved to disk. Live view and AI analysis still work."
                : recordingMode === "always"
                  ? "Record continuously in 5-minute segments. Uses the most storage."
                  : recordingMode === "on_motion"
                    ? "Start recording when motion is detected. Stop after motion ends."
                    : recordingMode === "on_object"
                      ? "Record only when specific objects are detected by the AI pipeline."
                      : "Save short clips around AI observations with pre and post buffers."}
            </p>
          </FieldRow>

          {recordingMode === "on_object" && (
            <FieldRow label="Record When Detected" hint="Which objects trigger recording. Type a label and press Enter.">
              <div>
                {recordingTriggerObjects.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {recordingTriggerObjects.map((label) => (
                      <span
                        key={label}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md border border-accent bg-accent/10 text-accent-foreground"
                      >
                        {label}
                        <button
                          type="button"
                          onClick={() => setRecordingTriggerObjects(recordingTriggerObjects.filter((l) => l !== label))}
                          className="text-accent-foreground/60 hover:text-accent-foreground ml-0.5"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                <input
                  type="text"
                  placeholder="Type object label and press Enter"
                  className={`${inputClass} text-xs`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      const val = (e.target as HTMLInputElement).value.trim().toLowerCase();
                      if (val && !recordingTriggerObjects.includes(val)) {
                        setRecordingTriggerObjects([...recordingTriggerObjects, val]);
                        (e.target as HTMLInputElement).value = "";
                      }
                    }
                  }}
                />
                <details className="mt-2">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground transition-colors">
                    Common labels
                  </summary>
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {[
                      "person", "car", "truck", "bus", "motorcycle", "bicycle",
                      "cat", "dog", "bird", "horse", "sheep", "cow",
                      "backpack", "suitcase", "umbrella",
                    ].filter((l) => !recordingTriggerObjects.includes(l)).map((label) => (
                      <button
                        key={label}
                        type="button"
                        onClick={() => setRecordingTriggerObjects([...recordingTriggerObjects, label])}
                        className="px-1.5 py-0.5 text-[10px] rounded border border-border text-muted-foreground hover:border-accent hover:text-accent-foreground transition-colors"
                      >
                        + {label}
                      </button>
                    ))}
                  </div>
                </details>
                {recordingTriggerObjects.length === 0 && (
                  <p className="text-[11px] text-muted-foreground mt-1.5">
                    No objects selected. Recording triggers on any detection.
                  </p>
                )}
              </div>
            </FieldRow>
          )}

          {recordingMode === "clip" && (
            <>
              <FieldRow label="Pre-buffer" hint="Seconds of footage to keep before the trigger event">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={30}
                    step={1}
                    value={recordingClipPre}
                    onChange={(e) => setRecordingClipPre(Number(e.target.value))}
                    className="flex-1 accent-accent"
                  />
                  <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                    {recordingClipPre}s
                  </span>
                </div>
              </FieldRow>

              <FieldRow label="Post-buffer" hint="Seconds to keep recording after the trigger event">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={60}
                    step={1}
                    value={recordingClipPost}
                    onChange={(e) => setRecordingClipPost(Number(e.target.value))}
                    className="flex-1 accent-accent"
                  />
                  <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                    {recordingClipPost}s
                  </span>
                </div>
              </FieldRow>
            </>
          )}

          <FieldRow label="Motion Sensitivity" hint="Higher = more sensitive">
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={motionSensitivity}
                onChange={(e) => setMotionSensitivity(Number(e.target.value))}
                className="flex-1 accent-accent"
              />
              <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                {(motionSensitivity * 100).toFixed(0)}%
              </span>
            </div>
          </FieldRow>
        </Section>

        {/* ── Authentication ── */}
        {supportsAuth && (
          <Section
            title="Authentication"
            description="Credentials for accessing the camera feed"
          >
            <FieldRow label="Username">
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                className={inputClass}
              />
            </FieldRow>

            <FieldRow label="Password" hint="Leave blank to keep current">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className={inputClass}
              />
            </FieldRow>

            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="flex-1 h-px bg-border" />
              or use token
              <span className="flex-1 h-px bg-border" />
            </div>

            <FieldRow label="Bearer Token" hint="For API-based cameras">
              <input
                type="password"
                value={authToken}
                onChange={(e) => setAuthToken(e.target.value)}
                placeholder="Token or API key"
                className={`${inputClass} font-mono text-xs`}
              />
            </FieldRow>
          </Section>
        )}

        {/* ── VLM / AI Analysis ── */}
        <Section
          title="AI Analysis"
          description="Configure which model analyzes this camera and how"
        >
          <FieldRow label="VLM Provider" hint="Leave on System Default to use global setting">
            <select
              value={vlmProviderId || ""}
              onChange={(e) => setVlmProviderId(e.target.value || null)}
              className={inputClass}
            >
              <option value="">
                System Default{activeProvider ? ` (${activeProvider.name})` : ""}
              </option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.default_model ? ` · ${p.default_model}` : ""}
                </option>
              ))}
            </select>
            {selectedProvider && (
              <p className="text-[11px] text-muted-foreground mt-1">
                {selectedProvider.kind} · {selectedProvider.base_url}
              </p>
            )}
          </FieldRow>

          <FieldRow label="Analysis Frequency" hint="How often to send frames to VLM">
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={300}
                step={5}
                value={vlmInterval}
                onChange={(e) => setVlmInterval(Number(e.target.value))}
                className="flex-1 accent-accent"
              />
              <span className="font-mono text-xs text-muted-foreground w-28 text-right">
                {formatInterval(vlmInterval)}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">
              {vlmInterval === 0
                ? "Analyze every motion keyframe. More API calls"
                : `Wait at least ${formatInterval(vlmInterval)} between VLM calls`}
            </p>
          </FieldRow>

          <FieldRow label="Trigger Condition" hint="When to send frames to VLM">
            <div className="flex gap-1.5 mb-2">
              {([
                { value: "always", label: "Always", desc: "Time-based, using frequency above" },
                { value: "on_object", label: "On Detection", desc: "Only when specific objects are detected" },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setVlmTrigger(opt.value)}
                  className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
                    vlmTrigger === opt.value
                      ? "border-accent bg-accent/10 text-accent-foreground"
                      : "border-border hover:border-muted-foreground text-muted-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {vlmTrigger === "always"
                ? "VLM runs on every keyframe (respecting frequency limit above)"
                : vlmTriggerObjects.length > 0
                  ? `VLM only runs when ${vlmTriggerObjects.join(", ")} detected by object detection`
                  : "VLM only runs when any object is detected"}
            </p>
          </FieldRow>

          {vlmTrigger === "on_object" && (
            <FieldRow label="Trigger Objects" hint="Type a label and press Enter. Works with any model's classes.">
              <div>
                {/* Selected tags */}
                {vlmTriggerObjects.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {vlmTriggerObjects.map((label) => (
                      <span
                        key={label}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md border border-accent bg-accent/10 text-accent-foreground"
                      >
                        {label}
                        <button
                          type="button"
                          onClick={() => setVlmTriggerObjects(vlmTriggerObjects.filter((l) => l !== label))}
                          className="text-accent-foreground/60 hover:text-accent-foreground ml-0.5"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}

                {/* Tag input */}
                <input
                  type="text"
                  placeholder="Type object label and press Enter"
                  className={`${inputClass} text-xs`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      const val = (e.target as HTMLInputElement).value.trim().toLowerCase();
                      if (val && !vlmTriggerObjects.includes(val)) {
                        setVlmTriggerObjects([...vlmTriggerObjects, val]);
                        (e.target as HTMLInputElement).value = "";
                      }
                    }
                  }}
                />

                {/* Quick-add suggestions */}
                <details className="mt-2">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground transition-colors">
                    Common labels
                  </summary>
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {[
                      "person", "car", "truck", "bus", "motorcycle", "bicycle",
                      "cat", "dog", "bird", "horse", "sheep", "cow",
                      "backpack", "suitcase", "umbrella", "handbag",
                      "cell phone", "laptop", "tv", "chair", "couch", "bed",
                      "bottle", "cup", "knife", "scissors",
                      "fire hydrant", "stop sign", "traffic light",
                      "skateboard", "surfboard", "sports ball", "kite",
                      "teddy bear", "clock", "book", "potted plant",
                    ].filter((l) => !vlmTriggerObjects.includes(l)).map((label) => (
                      <button
                        key={label}
                        type="button"
                        onClick={() => setVlmTriggerObjects([...vlmTriggerObjects, label])}
                        className="px-1.5 py-0.5 text-[10px] rounded border border-border text-muted-foreground hover:border-accent hover:text-accent-foreground transition-colors"
                      >
                        + {label}
                      </button>
                    ))}
                  </div>
                </details>

                {vlmTriggerObjects.length === 0 && (
                  <p className="text-[11px] text-muted-foreground mt-1.5">
                    No objects selected. VLM will trigger on any detection.
                  </p>
                )}
              </div>
            </FieldRow>
          )}

          <FieldRow label="Max Tokens" hint="Response length limit">
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={50}
                max={1000}
                step={50}
                value={vlmMaxTokens}
                onChange={(e) => setVlmMaxTokens(Number(e.target.value))}
                className="flex-1 accent-accent"
              />
              <span className="font-mono text-xs text-muted-foreground w-16 text-right">
                {vlmMaxTokens}
              </span>
            </div>
          </FieldRow>

          <FieldRow label="Custom Prompt" hint="Override system prompt for this camera">
            <textarea
              value={vlmPrompt}
              onChange={(e) => setVlmPrompt(e.target.value)}
              placeholder={DEFAULT_VLM_PROMPT}
              rows={4}
              className={`${inputClass} resize-y`}
            />
            {vlmPrompt.trim() && (
              <button
                type="button"
                onClick={() => setVlmPrompt("")}
                className="text-[11px] text-muted-foreground hover:text-danger mt-1 transition-colors"
              >
                Reset to default
              </button>
            )}
          </FieldRow>
        </Section>

        {/* Detection */}
        <Section
          title="Detection"
          description="Object and face detection models for this camera"
        >
          <FieldRow label="Scene Mode" hint="Controls how unknown faces are handled">
            <div className="space-y-2">
              <div className="flex gap-2">
                {(["indoor", "outdoor"] as const).map((mode) => (
                  <button key={mode} onClick={() => setSceneMode(mode)}
                    className={`flex-1 px-3 py-2 text-xs rounded-lg transition-colors ${sceneMode === mode ? "bg-accent/15 text-accent-foreground font-medium border border-accent/30" : "text-muted-foreground border border-border hover:text-foreground hover:bg-muted/50"}`}>
                    {mode === "indoor" ? "Indoor" : "Outdoor"}
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {sceneMode === "outdoor"
                  ? "Outdoor mode will still recognize people you have already named, but will not try to identify unknown faces. This prevents your People page from filling up with strangers walking by."
                  : "Indoor mode will track all faces and suggest unknown people for you to name."}
              </p>
            </div>
          </FieldRow>

          <FieldRow label="Object Detection" hint="Enable YOLO-based object recognition">
            <Toggle
              checked={detectObjects}
              onChange={setDetectObjects}
              label={detectObjects ? "Enabled" : "Disabled"}
            />
          </FieldRow>

          {detectObjects && (
            <>
              {/* Model list */}
              <FieldRow label="Detection Models" hint="Run multiple models for better accuracy">
                <div className="space-y-2">
                  {detectionModels.map((m, i) => (
                    <div key={i} className="flex items-center gap-2 p-2.5 rounded-md border border-border bg-background">
                      <Toggle
                        checked={m.enabled}
                        onChange={(v) => {
                          const updated = [...detectionModels];
                          updated[i] = { ...m, enabled: v };
                          setDetectionModels(updated);
                        }}
                      />
                      <input
                        type="text"
                        value={m.model}
                        onChange={(e) => {
                          const updated = [...detectionModels];
                          updated[i] = { ...m, model: e.target.value };
                          setDetectionModels(updated);
                        }}
                        placeholder="yolov8n.pt"
                        className="flex-1 px-2 py-1 text-xs font-mono rounded border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                      />
                      <div className="flex items-center gap-1.5 min-w-[140px]">
                        <input
                          type="range"
                          min={0.05}
                          max={0.95}
                          step={0.05}
                          value={m.confidence}
                          onChange={(e) => {
                            const updated = [...detectionModels];
                            updated[i] = { ...m, confidence: Number(e.target.value) };
                            setDetectionModels(updated);
                          }}
                          className="flex-1 accent-accent"
                        />
                        <span className="font-mono text-[11px] text-muted-foreground w-8 text-right">
                          {(m.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setDetectionModels(detectionModels.filter((_, j) => j !== i));
                        }}
                        className="text-muted-foreground hover:text-danger transition-colors text-sm px-1"
                        title="Remove model"
                      >
                        ×
                      </button>
                    </div>
                  ))}

                  <button
                    type="button"
                    onClick={() => {
                      setDetectionModels([
                        ...detectionModels,
                        { model: "yolov8n.pt", confidence: 0.35, enabled: true, label_filter: [] },
                      ]);
                    }}
                    className="w-full py-2 text-xs text-muted-foreground hover:text-foreground border border-dashed border-border rounded-md hover:border-accent transition-colors"
                  >
                    + Add detection model
                  </button>

                  {detectionModels.length === 0 && (
                    <p className="text-[11px] text-muted-foreground">
                      No models configured. Single YOLO model with {(objectConfidence * 100).toFixed(0)}% confidence used as fallback.
                    </p>
                  )}
                </div>
              </FieldRow>

              {/* Fallback confidence (shown when no models configured) */}
              {detectionModels.length === 0 && (
                <FieldRow label="Confidence Threshold" hint="Min confidence for default YOLO model">
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min={0.05}
                      max={0.95}
                      step={0.05}
                      value={objectConfidence}
                      onChange={(e) => setObjectConfidence(Number(e.target.value))}
                      className="flex-1 accent-accent"
                    />
                    <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                      {(objectConfidence * 100).toFixed(0)}%
                    </span>
                  </div>
                </FieldRow>
              )}

              {/* Merge strategy (only when multiple models) */}
              {detectionModels.length > 1 && (
                <>
                  <FieldRow label="Merge Strategy" hint="How to combine results from multiple models">
                    <div className="flex gap-1.5">
                      {([
                        { value: "any", label: "Any Model", desc: "Union of all detections" },
                        { value: "consensus", label: "Consensus", desc: "Multiple models must agree" },
                        { value: "best", label: "Best Score", desc: "Highest confidence per object" },
                      ] as const).map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => setDetectionMerge(opt.value)}
                          className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
                            detectionMerge === opt.value
                              ? "border-accent bg-accent/10 text-accent-foreground"
                              : "border-border hover:border-muted-foreground text-muted-foreground"
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-1.5">
                      {detectionMerge === "any"
                        ? "Keep all detections from all models. Overlapping boxes get de-duplicated."
                        : detectionMerge === "consensus"
                          ? `Only keep objects detected by at least ${detectionConsensusMin} model${detectionConsensusMin !== 1 ? "s" : ""}.`
                          : "For each detected object region, keep only the highest confidence result."}
                    </p>
                  </FieldRow>

                  {detectionMerge === "consensus" && (
                    <FieldRow label="Min Agreement" hint="Number of models that must detect the same object">
                      <div className="flex items-center gap-3">
                        <input
                          type="range"
                          min={2}
                          max={Math.max(2, detectionModels.filter((m) => m.enabled).length)}
                          step={1}
                          value={detectionConsensusMin}
                          onChange={(e) => setDetectionConsensusMin(Number(e.target.value))}
                          className="flex-1 accent-accent"
                        />
                        <span className="font-mono text-xs text-muted-foreground w-12 text-right">
                          {detectionConsensusMin} / {detectionModels.filter((m) => m.enabled).length}
                        </span>
                      </div>
                    </FieldRow>
                  )}
                </>
              )}
            </>
          )}

          <FieldRow label="Face Detection" hint="Detect and match known people">
            <Toggle
              checked={detectFaces}
              onChange={setDetectFaces}
              label={detectFaces ? "Enabled" : "Disabled"}
            />
          </FieldRow>
        </Section>

        {/* ── Activity Digest ── */}
        <Section
          title="Activity Digest"
          description="Configure the automatic activity summary shown on the cameras page"
        >
          <FieldRow label="Digest">
            <Toggle
              checked={digestEnabled}
              onChange={setDigestEnabled}
              label={digestEnabled ? "Enabled" : "Disabled"}
            />
          </FieldRow>

          {digestEnabled && (
            <>
              <FieldRow label="Time Period" hint="How far back to look for activity">
                <div className="flex gap-1.5 flex-wrap">
                  {(["1h", "6h", "12h", "24h", "48h", "7d"] as const).map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setDigestPeriod(p)}
                      className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
                        digestPeriod === p
                          ? "border-accent bg-accent/10 text-accent-foreground"
                          : "border-border hover:border-muted-foreground text-muted-foreground"
                      }`}
                    >
                      {p === "1h" ? "1 hour"
                        : p === "6h" ? "6 hours"
                        : p === "12h" ? "12 hours"
                        : p === "24h" ? "24 hours"
                        : p === "48h" ? "2 days"
                        : "7 days"}
                    </button>
                  ))}
                </div>
              </FieldRow>

              <FieldRow label="Digest Model" hint="Which model generates the summary">
                <select
                  value={digestProviderId || ""}
                  onChange={(e) => setDigestProviderId(e.target.value || null)}
                  className={inputClass}
                >
                  <option value="">
                    System Default{activeProvider ? ` (${activeProvider.name})` : ""}
                  </option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.default_model ? ` · ${p.default_model}` : ""}
                    </option>
                  ))}
                </select>
              </FieldRow>

              <FieldRow label="Digest Prompt" hint="Custom instructions for generating the summary">
                <textarea
                  value={digestPrompt}
                  onChange={(e) => setDigestPrompt(e.target.value)}
                  placeholder="You are Nurby, an AI camera monitoring assistant. Summarize the following camera observations into a brief digest. Be concise (2-4 sentences). Mention key activity, people, and patterns."
                  rows={3}
                  className={`${inputClass} resize-y`}
                />
                {digestPrompt.trim() && (
                  <button
                    type="button"
                    onClick={() => setDigestPrompt("")}
                    className="text-[11px] text-muted-foreground hover:text-danger mt-1 transition-colors"
                  >
                    Reset to default
                  </button>
                )}
              </FieldRow>
            </>
          )}
        </Section>

        {/* ── Retention ── */}
        <Section
          title="Storage Retention"
          description="Control how long recordings are kept on disk for this camera"
        >
          <FieldRow label="Retention Policy">
            <div className="flex gap-1.5">
              {([
                { value: "none", label: "Keep Forever" },
                { value: "time", label: "By Age" },
                { value: "size", label: "By Size" },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setRetentionMode(opt.value)}
                  className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
                    retentionMode === opt.value
                      ? "border-accent bg-accent/10 text-accent-foreground"
                      : "border-border hover:border-muted-foreground text-muted-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </FieldRow>

          {retentionMode === "time" && (
            <FieldRow label="Keep Recordings For" hint="Delete recordings older than this">
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={1}
                  max={365}
                  step={1}
                  value={retentionDays}
                  onChange={(e) => setRetentionDays(Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                  {retentionDays < 30
                    ? `${retentionDays}d`
                    : retentionDays < 365
                      ? `${Math.floor(retentionDays / 30)}mo ${retentionDays % 30 ? `${retentionDays % 30}d` : ""}`.trim()
                      : `${Math.floor(retentionDays / 365)}y`}
                </span>
              </div>
              <div className="flex gap-2 mt-2">
                {[7, 14, 30, 90, 180, 365].map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setRetentionDays(d)}
                    className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                      retentionDays === d
                        ? "border-accent text-accent-foreground"
                        : "border-border text-muted-foreground hover:border-muted-foreground"
                    }`}
                  >
                    {d < 30 ? `${d}d` : d < 365 ? `${d / 30}mo` : "1y"}
                  </button>
                ))}
              </div>
            </FieldRow>
          )}

          {retentionMode === "size" && (
            <FieldRow label="Max Storage" hint="Delete oldest recordings when limit is reached">
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={1}
                  max={500}
                  step={1}
                  value={retentionGb}
                  onChange={(e) => setRetentionGb(Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="font-mono text-xs text-muted-foreground w-16 text-right">
                  {retentionGb < 1000 ? `${retentionGb} GB` : `${(retentionGb / 1000).toFixed(1)} TB`}
                </span>
              </div>
              <div className="flex gap-2 mt-2">
                {[5, 10, 25, 50, 100, 250, 500].map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => setRetentionGb(g)}
                    className={`px-2 py-0.5 text-[11px] rounded border transition-colors ${
                      retentionGb === g
                        ? "border-accent text-accent-foreground"
                        : "border-border text-muted-foreground hover:border-muted-foreground"
                    }`}
                  >
                    {g} GB
                  </button>
                ))}
              </div>
            </FieldRow>
          )}

          {retentionMode !== "none" && (
            <div className="rounded-md bg-warning/5 border border-warning/20 px-3 py-2">
              <p className="text-xs text-warning">
                {retentionMode === "time"
                  ? `Recordings older than ${retentionDays} day${retentionDays !== 1 ? "s" : ""} will be automatically deleted from disk.`
                  : `When recordings exceed ${retentionGb} GB, oldest files will be deleted to make space.`}
              </p>
            </div>
          )}
        </Section>

        {/* ── PTZ Control ── */}
        {streamType === "rtsp" && (
          <Section
            title="PTZ Control"
            description="Pan, tilt, and zoom controls for ONVIF-compatible cameras"
          >
            <PTZControlPanel cameraId={cameraId} />
          </Section>
        )}

        {/* ── Motion Zones ── */}
        <Section
          title="Motion Zones"
          description="Define include and exclude zones for motion detection"
        >
          <ZoneEditorCanvas
            zones={motionZones}
            onChange={setMotionZones}
            width={camera.width || 1920}
            height={camera.height || 1080}
          />
        </Section>

        {/* ── Danger Zone ── */}
        <Section title="Danger Zone">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-foreground">Delete this camera</p>
              <p className="text-xs text-muted-foreground">
                Removes config and stops stream. Recordings remain on disk.
              </p>
            </div>
            {!deleteConfirm ? (
              <button
                onClick={() => setDeleteConfirm(true)}
                className="px-3 py-1.5 text-sm rounded-md border border-danger/30 text-danger hover:bg-danger/10 transition-colors"
              >
                Delete
              </button>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => setDeleteConfirm(false)}
                  className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDelete}
                  className="px-3 py-1.5 text-sm rounded-md bg-danger text-white hover:opacity-90 transition-opacity"
                >
                  Confirm Delete
                </button>
              </div>
            )}
          </div>
        </Section>
      </div>

      {/* Sticky save bar */}
      <div className="sticky bottom-0 mt-6 -mx-6 px-6 py-3 bg-background/80 backdrop-blur-sm border-t border-border flex items-center justify-between">
        <div>
          {error && <p className="text-sm text-danger">{error}</p>}
          {saved && (
            <p className="text-sm text-accent">Settings saved</p>
          )}
        </div>
        <div className="flex gap-2">
          <Link
            href="/"
            className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
          >
            Cancel
          </Link>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !streamUrl.trim()}
            className="px-4 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
