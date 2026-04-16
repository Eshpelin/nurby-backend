"use client";

import { useCallback, useEffect, useState } from "react";
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
  vlm_provider_id: string | null;
  vlm_prompt: string | null;
  vlm_interval: number;
  vlm_max_tokens: number;
  detect_objects: boolean;
  detect_faces: boolean;
  object_confidence: number;
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

export default function CameraConfigPage() {
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
  const [vlmProviderId, setVlmProviderId] = useState<string | null>(null);
  const [vlmPrompt, setVlmPrompt] = useState("");
  const [vlmInterval, setVlmInterval] = useState(0);
  const [vlmMaxTokens, setVlmMaxTokens] = useState(200);
  const [detectObjects, setDetectObjects] = useState(true);
  const [detectFaces, setDetectFaces] = useState(true);
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

  const fetchData = useCallback(async () => {
    try {
      const [camRes, provRes] = await Promise.all([
        fetch(`/api/cameras/${cameraId}`),
        fetch(`/api/providers`),
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
      setVlmProviderId(cam.vlm_provider_id ?? null);
      setVlmPrompt(cam.vlm_prompt || "");
      setVlmInterval(cam.vlm_interval ?? 0);
      setVlmMaxTokens(cam.vlm_max_tokens ?? 200);
      setDetectObjects(cam.detect_objects ?? true);
      setDetectFaces(cam.detect_faces ?? true);
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
        vlm_provider_id: vlmProviderId,
        vlm_prompt: vlmPrompt.trim() || null,
        vlm_interval: vlmInterval,
        vlm_max_tokens: vlmMaxTokens,
        detect_objects: detectObjects,
        detect_faces: detectFaces,
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
      };

      if (username.trim()) payload.username = username.trim();
      if (password) payload.password = password;
      if (authToken.trim()) payload.auth_token = authToken.trim();

      const res = await fetch(`/api/cameras/${cameraId}`, {
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
      const res = await fetch(`/api/cameras/${cameraId}`, { method: "DELETE" });
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

          <FieldRow label="Recording">
            <Toggle
              checked={recordingEnabled}
              onChange={setRecordingEnabled}
              label={recordingEnabled ? "Recording to disk" : "Not recording"}
            />
          </FieldRow>

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
