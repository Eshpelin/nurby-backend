"use client";

import { useAuth } from "@/lib/auth";

import { useCallback, useEffect, useRef, useState } from "react";
import { PersonaPicker } from "@/components/PersonaPicker";
import type { PersonaPatch } from "@/lib/camera-personas";
import { ConversationCard } from "@/components/ConversationCard";
import { SummaryCard } from "@/components/SummaryCard";
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
  vlm_max_input_tokens: number | null;
  vlm_refiner_provider_id: string | null;
  vlm_refiner_trigger_objects: string[] | null;
  vlm_refiner_keywords: string[] | null;
  vlm_refiner_max_tokens: number | null;
  vlm_refiner_max_input_tokens: number | null;
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
  summary_provider_id: string | null;
  summary_mode: string;
  summary_period_seconds: number;
  summary_event_quiet_seconds: number;
  summary_event_trigger_objects: string[] | null;
  summary_event_min_duration_seconds: number;
  summary_max_tokens: number;
  conversation_gap_seconds: number;
  conversation_summary_enabled: boolean;
  conversation_min_messages_for_summary: number;
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

interface DetectionModelOption {
  value: string;
  label: string;
  hint: string;
  family: "yolov8" | "yolo11" | "yolo-world" | "rtdetr" | "oiv7";
}

const DETECTION_MODEL_CATALOG: DetectionModelOption[] = [
  { value: "yolov8n.pt", label: "YOLOv8 Nano",   hint: "Fastest. 80 COCO classes. ~6ms on CPU.",       family: "yolov8" },
  { value: "yolov8s.pt", label: "YOLOv8 Small",  hint: "Balanced speed and accuracy. 80 COCO classes.", family: "yolov8" },
  { value: "yolov8m.pt", label: "YOLOv8 Medium", hint: "Better accuracy, slower. 80 COCO classes.",     family: "yolov8" },
  { value: "yolov8l.pt", label: "YOLOv8 Large",  hint: "High accuracy. GPU recommended.",               family: "yolov8" },
  { value: "yolov8x.pt", label: "YOLOv8 XLarge", hint: "Top YOLOv8 accuracy. GPU strongly advised.",    family: "yolov8" },
  { value: "yolo11n.pt", label: "YOLO11 Nano",   hint: "Newer gen. Slightly better than v8n.",          family: "yolo11" },
  { value: "yolo11s.pt", label: "YOLO11 Small",  hint: "Newer gen. Drop-in upgrade for v8s.",           family: "yolo11" },
  { value: "yolo11m.pt", label: "YOLO11 Medium", hint: "Newer gen medium. Good accuracy tradeoff.",     family: "yolo11" },
  { value: "yolo11l.pt", label: "YOLO11 Large",  hint: "Newer gen large. GPU recommended.",             family: "yolo11" },
  { value: "yolo11x.pt", label: "YOLO11 XLarge", hint: "Top-tier detection, GPU needed for realtime.",  family: "yolo11" },
  { value: "yolov8n-oiv7.pt", label: "YOLOv8n Open Images", hint: "600+ classes (furniture, tools, food, animals).", family: "oiv7" },
  { value: "yolov8s-oiv7.pt", label: "YOLOv8s Open Images", hint: "600+ classes, better accuracy.",     family: "oiv7" },
  { value: "yolov8s-world.pt", label: "YOLO-World Small", hint: "Open-vocabulary. Detect arbitrary class names.", family: "yolo-world" },
  { value: "yolov8m-world.pt", label: "YOLO-World Medium", hint: "Open-vocabulary, better recall.",    family: "yolo-world" },
  { value: "yolov8l-worldv2.pt", label: "YOLO-World v2 Large", hint: "Top open-vocab accuracy.",        family: "yolo-world" },
  { value: "rtdetr-l.pt", label: "RT-DETR Large",  hint: "Transformer-based. Strong accuracy.",          family: "rtdetr" },
  { value: "rtdetr-x.pt", label: "RT-DETR XLarge", hint: "Top RT-DETR. GPU required for realtime.",      family: "rtdetr" },
];

function DetectionModelSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [customMode, setCustomMode] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const match = DETECTION_MODEL_CATALOG.find((m) => m.value === value);
  const isCustom = !match && value.length > 0;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (customMode || isCustom) {
    return (
      <div className="flex-1 flex items-center gap-1">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="custom-model.pt"
          className="flex-1 px-2 py-1 text-xs font-mono rounded border border-border bg-card text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
          autoFocus
        />
        <button
          type="button"
          onClick={() => { setCustomMode(false); onChange("yolov8n.pt"); }}
          className="text-[10px] text-muted-foreground hover:text-foreground px-1.5"
          title="Pick from catalog instead"
        >Catalog</button>
      </div>
    );
  }

  return (
    <div ref={ref} className="flex-1 relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-2 py-1 rounded border border-border bg-card text-xs hover:border-muted-foreground/40 focus:outline-none focus:border-accent transition-colors"
      >
        <span className="min-w-0 text-left">
          <span className="block truncate font-medium">{match?.label || "Pick a model"}</span>
          <span className="block truncate text-[10px] text-muted-foreground font-mono">{match?.value || value}</span>
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`text-muted-foreground flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`}>
          <path d="m6 9 6 6 6-6"/>
        </svg>
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-[28rem] max-w-[80vw] right-0 rounded-md border border-border bg-card shadow-lg max-h-80 overflow-y-auto py-1">
          {(["yolov8", "yolo11", "yolo-world", "oiv7", "rtdetr"] as const).map((fam) => {
            const group = DETECTION_MODEL_CATALOG.filter((m) => m.family === fam);
            if (group.length === 0) return null;
            const famLabel = {
              "yolov8": "YOLOv8 (COCO 80 classes)",
              "yolo11": "YOLO11 (COCO 80 classes, newer)",
              "yolo-world": "YOLO-World (open vocabulary)",
              "oiv7": "Open Images V7 (600+ classes)",
              "rtdetr": "RT-DETR (transformer detector)",
            }[fam];
            return (
              <div key={fam} className="py-1">
                <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground font-medium">{famLabel}</div>
                {group.map((m) => {
                  const selected = m.value === value;
                  return (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => { onChange(m.value); setOpen(false); }}
                      className={`w-full text-left px-3 py-1.5 flex items-start justify-between gap-2 hover:bg-muted/60 ${selected ? "bg-muted/40" : ""}`}
                    >
                      <span className="min-w-0">
                        <span className="block text-xs font-medium truncate">{m.label}</span>
                        <span className="block text-[10px] text-muted-foreground truncate">{m.hint}</span>
                        <span className="block text-[10px] text-muted-foreground/70 font-mono">{m.value}</span>
                      </span>
                      {selected && (
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-accent flex-shrink-0 mt-0.5">
                          <path d="M20 6 9 17l-5-5"/>
                        </svg>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
          <div className="border-t border-border mt-1 pt-1">
            <button
              type="button"
              onClick={() => { setOpen(false); setCustomMode(true); onChange(""); }}
              className="w-full text-left px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            >Enter custom model filename.</button>
          </div>
        </div>
      )}
    </div>
  );
}

function LabelPicker({
  selected,
  available,
  loading,
  onChange,
  placeholder,
  activeModels,
  onAddModel,
}: {
  selected: string[];
  available: string[];
  loading: boolean;
  onChange: (labels: string[]) => void;
  placeholder?: string;
  activeModels?: string[];
  onAddModel?: (model: string) => void;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const remaining = available.filter((l) => !selected.includes(l));
  const filtered = q ? remaining.filter((l) => l.toLowerCase().includes(q)) : remaining;

  const needsModel = (activeModels?.length || 0) === 0;

  return (
    <div>
      {activeModels && activeModels.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          <span className="text-[10px] text-muted-foreground self-center">Labels sourced from.</span>
          {activeModels.map((m) => (
            <span key={m} className="px-1.5 py-0.5 text-[10px] font-mono rounded border border-border bg-muted/30 text-muted-foreground">
              {m}
            </span>
          ))}
        </div>
      )}

      {needsModel && onAddModel && (
        <div className="mb-2 rounded-md border border-dashed border-amber-500/40 bg-amber-500/5 p-2.5">
          <p className="text-[11px] text-amber-300 mb-1.5">
            Pick a detection model first. Labels come from whichever model you choose.
          </p>
          <DetectionModelSelect
            value="yolov8n.pt"
            onChange={(v) => { if (v) onAddModel(v); }}
          />
        </div>
      )}

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selected.map((label) => (
            <span
              key={label}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md border border-accent bg-accent/10 text-accent-foreground"
            >
              {label}
              <button
                type="button"
                onClick={() => onChange(selected.filter((l) => l !== label))}
                className="text-accent-foreground/60 hover:text-accent-foreground ml-0.5"
              >×</button>
            </span>
          ))}
        </div>
      )}
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={placeholder || "Search labels."}
        className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background focus:outline-none focus:border-accent"
        onKeyDown={(e) => {
          if (e.key === "Enter" && q) {
            e.preventDefault();
            const exact = available.find((l) => l.toLowerCase() === q);
            const pick = exact || q;
            if (!selected.includes(pick)) onChange([...selected, pick]);
            setQuery("");
          }
        }}
      />
      <div className="mt-2 max-h-40 overflow-y-auto rounded-md border border-border bg-background/40 p-1.5">
        {loading ? (
          <p className="text-[11px] text-muted-foreground px-1 py-2">Loading labels from model.</p>
        ) : available.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-1 py-2">
            {needsModel
              ? "Pick a model above to see its labels."
              : "Model loaded no classes. First-run download may still be in progress, or the model is open-vocabulary. Type a label and press Enter."}
          </p>
        ) : filtered.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-1 py-2">
            {q ? "No matches. Press Enter to add as custom." : "All labels added."}
          </p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {filtered.slice(0, 80).map((label) => (
              <button
                key={label}
                type="button"
                onClick={() => onChange([...selected, label])}
                className="px-1.5 py-0.5 text-[10px] rounded border border-border text-muted-foreground hover:border-accent hover:text-accent-foreground transition-colors"
              >+ {label}</button>
            ))}
            {filtered.length > 80 && (
              <span className="text-[10px] text-muted-foreground self-center px-1">
                +{filtered.length - 80} more. keep typing to narrow.
              </span>
            )}
          </div>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground mt-1">
        {available.length > 0 ? `${available.length} labels from selected model${available.length === 1 ? "" : "s"}.` : ""}
      </p>
    </div>
  );
}

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

function KeywordChipInput({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const cleaned = draft
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (cleaned.length === 0) return;
    const next = Array.from(new Set([...values, ...cleaned]));
    onChange(next);
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 min-h-[2.25rem] px-2 py-1 rounded-md border border-border bg-background focus-within:border-accent">
      {values.map((v) => (
        <span
          key={v}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-accent/15 text-accent border border-accent/30"
        >
          {v}
          <button
            type="button"
            onClick={() => onChange(values.filter((x) => x !== v))}
            className="text-accent/70 hover:text-accent"
            aria-label={`Remove ${v}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          } else if (e.key === "Backspace" && !draft && values.length > 0) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={commit}
        placeholder={values.length === 0 ? placeholder : ""}
        className="flex-1 min-w-[8rem] bg-transparent text-sm focus:outline-none"
      />
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
  type: "include" | "exclude" | "loiter" | "tripwire";
  // Seconds before a loiter zone fires. Ignored for other types.
  loiter_threshold_seconds?: number;
  // Direction filter for tripwires. "any" | "in" | "out".
  direction?: string;
}

const ZONE_COLORS: Record<string, { fill: string; stroke: string; dot: string; ui: string }> = {
  include:  { fill: "rgba(34,197,94,0.2)",  stroke: "#22c55e", dot: "bg-green-500",  ui: "border-green-500 bg-green-500/10 text-green-400" },
  exclude:  { fill: "rgba(239,68,68,0.2)",  stroke: "#ef4444", dot: "bg-red-500",    ui: "border-red-500 bg-red-500/10 text-red-400" },
  loiter:   { fill: "rgba(245,158,11,0.2)", stroke: "#f59e0b", dot: "bg-amber-500",  ui: "border-amber-500 bg-amber-500/10 text-amber-400" },
  tripwire: { fill: "rgba(99,102,241,0.2)", stroke: "#6366f1", dot: "bg-indigo-500", ui: "border-indigo-500 bg-indigo-500/10 text-indigo-400" },
};

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
  const [zoneType, setZoneType] = useState<"include" | "exclude" | "loiter" | "tripwire">("include");

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
      const colors = ZONE_COLORS[zone.type] || ZONE_COLORS.include;
      ctx.beginPath();
      ctx.moveTo(zone.points[0][0] * scaleX, zone.points[0][1] * scaleY);
      zone.points.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p[0] * scaleX, p[1] * scaleY);
      });
      if (zone.type === "tripwire") {
        // Leave open. draw as a thick line with an arrow indicator for direction.
        ctx.strokeStyle = colors.stroke;
        ctx.lineWidth = 3;
        ctx.stroke();
        // Arrow for direction ("in" default forward, "out" backward, "any" double-head).
        const a = [zone.points[0][0] * scaleX, zone.points[0][1] * scaleY];
        const b = [zone.points[1][0] * scaleX, zone.points[1][1] * scaleY];
        const mx = (a[0] + b[0]) / 2;
        const my = (a[1] + b[1]) / 2;
        const nx = -(b[1] - a[1]);
        const ny = (b[0] - a[0]);
        const nlen = Math.sqrt(nx * nx + ny * ny) || 1;
        const nxu = (nx / nlen) * 10;
        const nyu = (ny / nlen) * 10;
        const dir = zone.direction || "any";
        ctx.fillStyle = colors.stroke;
        ctx.beginPath();
        if (dir === "in" || dir === "any") {
          ctx.moveTo(mx, my);
          ctx.lineTo(mx + nxu - (b[0] - a[0]) * 0.03, my + nyu - (b[1] - a[1]) * 0.03);
          ctx.lineTo(mx + nxu + (b[0] - a[0]) * 0.03, my + nyu + (b[1] - a[1]) * 0.03);
          ctx.closePath();
        }
        if (dir === "out" || dir === "any") {
          ctx.moveTo(mx, my);
          ctx.lineTo(mx - nxu - (b[0] - a[0]) * 0.03, my - nyu - (b[1] - a[1]) * 0.03);
          ctx.lineTo(mx - nxu + (b[0] - a[0]) * 0.03, my - nyu + (b[1] - a[1]) * 0.03);
          ctx.closePath();
        }
        ctx.fill();
      } else {
        ctx.closePath();
        ctx.fillStyle = colors.fill;
        ctx.fill();
        ctx.strokeStyle = colors.stroke;
        ctx.lineWidth = 2;
        ctx.stroke();
      }

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
      const colors = ZONE_COLORS[zoneType] || ZONE_COLORS.include;
      ctx.beginPath();
      ctx.moveTo(currentPoints[0][0], currentPoints[0][1]);
      currentPoints.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p[0], p[1]);
      });
      ctx.strokeStyle = colors.stroke;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw points
      currentPoints.forEach((p) => {
        ctx.beginPath();
        ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
        ctx.fillStyle = colors.stroke;
        ctx.fill();
      });
    }
  }, [zones, currentPoints, scaleX, scaleY, canvasWidth, canvasHeight, zoneType]);

  useEffect(() => {
    drawZones();
  }, [drawZones]);

  const commitZone = useCallback((points: number[][]) => {
    const scaledPoints = points.map((p) => [
      Math.round(p[0] / scaleX),
      Math.round(p[1] / scaleY),
    ]);
    const newZone: MotionZone = {
      name: `${zoneType === "tripwire" ? "Tripwire" : zoneType === "loiter" ? "Loiter" : "Zone"} ${zones.length + 1}`,
      points: scaledPoints,
      type: zoneType,
    };
    if (zoneType === "loiter") newZone.loiter_threshold_seconds = 30;
    if (zoneType === "tripwire") newZone.direction = "any";
    onChange([...zones, newZone]);
    setDrawing(false);
    setCurrentPoints([]);
  }, [onChange, scaleX, scaleY, zoneType, zones]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (!drawing) {
      setDrawing(true);
      setCurrentPoints([[x, y]]);
      return;
    }
    const next = [...currentPoints, [x, y]];
    // Tripwire auto-finishes after 2 points.
    if (zoneType === "tripwire" && next.length === 2) {
      commitZone(next);
      return;
    }
    setCurrentPoints(next);
  };

  const finishZone = () => {
    if (zoneType === "tripwire") {
      if (currentPoints.length !== 2) return;
    } else if (currentPoints.length < 3) {
      return;
    }
    commitZone(currentPoints);
  };

  const removeZone = (index: number) => {
    onChange(zones.filter((_, i) => i !== index));
  };

  const updateZone = (index: number, patch: Partial<MotionZone>) => {
    onChange(zones.map((z, i) => (i === index ? { ...z, ...patch } : z)));
  };

  const zoneTypeButtons: { value: MotionZone["type"]; label: string }[] = [
    { value: "include", label: "Include" },
    { value: "exclude", label: "Exclude" },
    { value: "loiter", label: "Loiter" },
    { value: "tripwire", label: "Tripwire" },
  ];

  const needMin = zoneType === "tripwire" ? 2 : 3;
  const canFinish = currentPoints.length >= needMin;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 mb-2">
        {zoneTypeButtons.map((b) => (
          <button
            key={b.value}
            type="button"
            onClick={() => { setZoneType(b.value); setCurrentPoints([]); setDrawing(false); }}
            className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
              zoneType === b.value ? ZONE_COLORS[b.value].ui : "border-border text-muted-foreground"
            }`}
          >
            {b.label}
          </button>
        ))}
        {drawing && (
          <button
            type="button"
            onClick={finishZone}
            disabled={!canFinish}
            className="px-2.5 py-1.5 text-xs rounded-md border border-accent bg-accent/10 text-accent-foreground disabled:opacity-50"
          >
            Finish ({currentPoints.length}/{needMin === 2 ? "2" : `≥${needMin}`})
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
        {zoneType === "tripwire"
          ? "Click two points to drop a tripwire line. Auto-finishes on the second click."
          : `Click to add points. Finish when done (minimum ${needMin} points).`}
      </p>

      {zones.length > 0 && (
        <div className="space-y-1.5">
          {zones.map((zone, i) => (
            <div key={i} className="text-xs px-2 py-1.5 rounded border border-border space-y-1.5">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${ZONE_COLORS[zone.type]?.dot || "bg-muted"}`} />
                <input
                  type="text"
                  value={zone.name}
                  onChange={(e) => updateZone(i, { name: e.target.value })}
                  className="bg-transparent border-0 outline-none flex-1 min-w-0 font-medium focus:ring-1 focus:ring-accent rounded px-1"
                />
                <span className="text-muted-foreground">
                  {zone.type} · {zone.points.length} pts
                </span>
                <button
                  type="button"
                  onClick={() => removeZone(i)}
                  className="text-muted-foreground hover:text-red-400 transition-colors px-1"
                  aria-label="Remove zone"
                >×</button>
              </div>
              {zone.type === "loiter" && (
                <div className="flex items-center gap-2 pl-4">
                  <label className="text-muted-foreground">Fires after</label>
                  <input
                    type="number" min="1" max="3600"
                    value={zone.loiter_threshold_seconds ?? 30}
                    onChange={(e) => updateZone(i, { loiter_threshold_seconds: parseInt(e.target.value) || 30 })}
                    className="w-16 px-1.5 py-0.5 rounded bg-background border border-border text-xs"
                  />
                  <span className="text-muted-foreground">seconds inside the zone.</span>
                </div>
              )}
              {zone.type === "tripwire" && (
                <div className="flex items-center gap-2 pl-4">
                  <label className="text-muted-foreground">Direction</label>
                  <div className="flex gap-1">
                    {["any", "in", "out"].map((d) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => updateZone(i, { direction: d })}
                        className={`px-2 py-0.5 text-[11px] rounded border capitalize ${
                          (zone.direction || "any") === d
                            ? "border-indigo-500 bg-indigo-500/10 text-indigo-400"
                            : "border-border text-muted-foreground hover:bg-muted"
                        }`}
                      >{d}</button>
                    ))}
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
  const [vlmMaxInputTokens, setVlmMaxInputTokens] = useState<string>("");
  const [vlmRefinerProviderId, setVlmRefinerProviderId] = useState<string | null>(null);
  const [vlmRefinerTriggerObjects, setVlmRefinerTriggerObjects] = useState<string[]>(["person"]);
  const [vlmRefinerKeywords, setVlmRefinerKeywords] = useState<string[]>(["package", "delivery", "stranger", "weapon"]);
  const [vlmRefinerMaxTokens, setVlmRefinerMaxTokens] = useState<string>("");
  const [vlmRefinerMaxInputTokens, setVlmRefinerMaxInputTokens] = useState<string>("");
  const [vlmTrigger, setVlmTrigger] = useState("always");
  const [vlmTriggerObjects, setVlmTriggerObjects] = useState<string[]>([]);
  const [detectObjects, setDetectObjects] = useState(true);
  const [detectFaces, setDetectFaces] = useState(true);
  const [sceneMode, setSceneMode] = useState("indoor");
  const [objectConfidence, setObjectConfidence] = useState(0.35);
  const [detectionModels, setDetectionModels] = useState<{model: string; confidence: number; enabled: boolean; label_filter: string[]}[]>([]);
  const [detectionMerge, setDetectionMerge] = useState("any");
  const [modelClasses, setModelClasses] = useState<string[]>([]);
  const [modelClassesLoading, setModelClassesLoading] = useState(false);
  const [detectionConsensusMin, setDetectionConsensusMin] = useState(2);
  const [digestEnabled, setDigestEnabled] = useState(true);
  const [digestPeriod, setDigestPeriod] = useState("24h");
  const [digestProviderId, setDigestProviderId] = useState<string | null>(null);
  const [digestPrompt, setDigestPrompt] = useState("");
  const [retentionMode, setRetentionMode] = useState("none");
  const [retentionDays, setRetentionDays] = useState(30);
  const [retentionGb, setRetentionGb] = useState(50);
  const [summaryProviderId, setSummaryProviderId] = useState<string | null>(null);
  const [summaryMode, setSummaryMode] = useState("off");
  const [summaryPeriodSeconds, setSummaryPeriodSeconds] = useState(1800);
  const [summaryEventQuietSeconds, setSummaryEventQuietSeconds] = useState(60);
  const [summaryEventTriggerObjects, setSummaryEventTriggerObjects] = useState<string[]>(["person"]);
  const [summaryEventMinDurationSeconds, setSummaryEventMinDurationSeconds] = useState(5);
  const [summaryMaxTokens, setSummaryMaxTokens] = useState(400);
  const [conversationGapSeconds, setConversationGapSeconds] = useState(30);
  const [conversationSummaryEnabled, setConversationSummaryEnabled] = useState(true);
  const [conversationMinMessages, setConversationMinMessages] = useState(2);
  const [activeTab, setActiveTab] = useState<"settings" | "activity">("settings");
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
      setVlmMaxInputTokens(cam.vlm_max_input_tokens != null ? String(cam.vlm_max_input_tokens) : "");
      setVlmRefinerProviderId(cam.vlm_refiner_provider_id ?? null);
      setVlmRefinerTriggerObjects(cam.vlm_refiner_trigger_objects ?? ["person"]);
      setVlmRefinerKeywords(cam.vlm_refiner_keywords ?? ["package", "delivery", "stranger", "weapon"]);
      setVlmRefinerMaxTokens(cam.vlm_refiner_max_tokens != null ? String(cam.vlm_refiner_max_tokens) : "");
      setVlmRefinerMaxInputTokens(cam.vlm_refiner_max_input_tokens != null ? String(cam.vlm_refiner_max_input_tokens) : "");
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
      setSummaryProviderId(cam.summary_provider_id ?? null);
      setSummaryMode(cam.summary_mode ?? "off");
      setSummaryPeriodSeconds(cam.summary_period_seconds ?? 1800);
      setSummaryEventQuietSeconds(cam.summary_event_quiet_seconds ?? 60);
      setSummaryEventTriggerObjects(cam.summary_event_trigger_objects ?? ["person"]);
      setSummaryEventMinDurationSeconds(cam.summary_event_min_duration_seconds ?? 5);
      setSummaryMaxTokens(cam.summary_max_tokens ?? 400);
      setConversationGapSeconds(cam.conversation_gap_seconds ?? 30);
      setConversationSummaryEnabled(cam.conversation_summary_enabled ?? true);
      setConversationMinMessages(cam.conversation_min_messages_for_summary ?? 2);
      setMotionZones(cam.motion_zones ?? []);
    } catch {
      setError("Failed to load camera");
    } finally {
      setLoading(false);
      // Mark autosave as armed only after the hydrate burst settles.
      // setTimeout pushes past the React commit so the next render
      // tick is the first one autosave watches.
      setTimeout(() => {
        firstLoadDone.current = true;
      }, 0);
    }
  }, [cameraId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Fetch class names from the selected detection models. Falls back to
  // yolov8n.pt when the list is empty (matches backend fallback).
  useEffect(() => {
    const models = detectionModels.length > 0
      ? detectionModels.map((m) => m.model).filter(Boolean)
      : ["yolov8n.pt"];
    const params = models.map((m) => `model=${encodeURIComponent(m)}`).join("&");
    let cancelled = false;
    setModelClassesLoading(true);
    (async () => {
      try {
        const res = await authFetch(`/api/detection-models/classes?${params}`);
        if (!res.ok) throw new Error("fetch failed");
        const data = await res.json();
        if (!cancelled) setModelClasses(Array.isArray(data.classes) ? data.classes : []);
      } catch {
        if (!cancelled) setModelClasses([]);
      } finally {
        if (!cancelled) setModelClassesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [detectionModels, authFetch]);

  // Apply a persona preset by mapping each patch field onto its
  // corresponding setState. Only fields the persona defines are
  // touched. The user still has to click Save to persist.
  function applyPersona(patch: PersonaPatch) {
    if (patch.detect_objects !== undefined) setDetectObjects(patch.detect_objects);
    if (patch.detect_faces !== undefined) setDetectFaces(patch.detect_faces);
    if (patch.scene_mode !== undefined) setSceneMode(patch.scene_mode);
    if (patch.object_confidence !== undefined) setObjectConfidence(patch.object_confidence);
    if (patch.detection_models !== undefined) setDetectionModels(patch.detection_models);
    if (patch.vlm_trigger !== undefined) setVlmTrigger(patch.vlm_trigger);
    if (patch.vlm_trigger_objects !== undefined) setVlmTriggerObjects(patch.vlm_trigger_objects);
    if (patch.vlm_max_tokens !== undefined) setVlmMaxTokens(patch.vlm_max_tokens);
    if (patch.recording_mode !== undefined) setRecordingMode(patch.recording_mode);
    if (patch.recording_trigger_objects !== undefined) setRecordingTriggerObjects(patch.recording_trigger_objects);
    if (patch.recording_clip_pre !== undefined) setRecordingClipPre(patch.recording_clip_pre);
    if (patch.recording_clip_post !== undefined) setRecordingClipPost(patch.recording_clip_post);
    if (patch.retention_mode !== undefined) setRetentionMode(patch.retention_mode);
    if (patch.retention_days !== undefined) setRetentionDays(patch.retention_days);
    if (patch.retention_gb !== undefined) setRetentionGb(patch.retention_gb);
    if (patch.summary_mode !== undefined) setSummaryMode(patch.summary_mode);
    if (patch.summary_period_seconds !== undefined) setSummaryPeriodSeconds(patch.summary_period_seconds);
    if (patch.summary_event_quiet_seconds !== undefined) setSummaryEventQuietSeconds(patch.summary_event_quiet_seconds);
    if (patch.summary_event_trigger_objects !== undefined) setSummaryEventTriggerObjects(patch.summary_event_trigger_objects);
    if (patch.summary_event_min_duration_seconds !== undefined) setSummaryEventMinDurationSeconds(patch.summary_event_min_duration_seconds);
    if (patch.conversation_gap_seconds !== undefined) setConversationGapSeconds(patch.conversation_gap_seconds);
    if (patch.conversation_summary_enabled !== undefined) setConversationSummaryEnabled(patch.conversation_summary_enabled);
  }

  // Autosave plumbing. firstLoadDone flips true after fetchData
  // hydrates the form so the initial setState burst does not trigger
  // a save loop. autosaveTimer holds the pending debounce so a flurry
  // of slider drags collapses into a single PATCH.
  const firstLoadDone = useRef(false);
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        vlm_max_input_tokens: vlmMaxInputTokens.trim() ? Number(vlmMaxInputTokens) : null,
        vlm_refiner_provider_id: vlmRefinerProviderId,
        vlm_refiner_trigger_objects: vlmRefinerProviderId && vlmRefinerTriggerObjects.length > 0 ? vlmRefinerTriggerObjects : null,
        vlm_refiner_keywords: vlmRefinerProviderId && vlmRefinerKeywords.length > 0 ? vlmRefinerKeywords : null,
        vlm_refiner_max_tokens: vlmRefinerMaxTokens.trim() ? Number(vlmRefinerMaxTokens) : null,
        vlm_refiner_max_input_tokens: vlmRefinerMaxInputTokens.trim() ? Number(vlmRefinerMaxInputTokens) : null,
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
        summary_provider_id: summaryProviderId,
        summary_mode: summaryMode,
        summary_period_seconds: summaryPeriodSeconds,
        summary_event_quiet_seconds: summaryEventQuietSeconds,
        summary_event_trigger_objects: summaryEventTriggerObjects.length > 0 ? summaryEventTriggerObjects : null,
        summary_event_min_duration_seconds: summaryEventMinDurationSeconds,
        summary_max_tokens: summaryMaxTokens,
        conversation_gap_seconds: conversationGapSeconds,
        conversation_summary_enabled: conversationSummaryEnabled,
        conversation_min_messages_for_summary: conversationMinMessages,
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

  // Autosave. Watches every editable field and PATCHes the camera
  // 800ms after the last change. Skips the first hydrate burst via
  // the firstLoadDone ref so we never POST an immediate save on
  // mount.
  useEffect(() => {
    if (!firstLoadDone.current) return;
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      handleSave();
    }, 800);
    return () => {
      if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    name, streamUrl, streamType, locationLabel, username, password, authToken,
    snapshotInterval, motionSensitivity, recordingEnabled, recordingMode,
    recordingTriggerObjects, recordingClipPre, recordingClipPost,
    vlmProviderId, vlmPrompt, vlmInterval, vlmMaxTokens, vlmMaxInputTokens,
    vlmTrigger, vlmTriggerObjects,
    vlmRefinerProviderId, vlmRefinerTriggerObjects, vlmRefinerKeywords,
    vlmRefinerMaxTokens, vlmRefinerMaxInputTokens,
    detectObjects, detectFaces, sceneMode, objectConfidence,
    detectionModels, detectionMerge, detectionConsensusMin,
    digestEnabled, digestPeriod, digestProviderId, digestPrompt,
    retentionMode, retentionDays, retentionGb,
    summaryProviderId, summaryMode, summaryPeriodSeconds,
    summaryEventQuietSeconds, summaryEventTriggerObjects,
    summaryEventMinDurationSeconds, summaryMaxTokens,
    conversationGapSeconds, conversationSummaryEnabled, conversationMinMessages,
    motionZones,
  ]);

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
        <Link
          href={`/cameras/${cameraId}/audio`}
          className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground hover:border-foreground/30"
          title="Audio capture, transcription, and recent transcripts"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
          Audio
        </Link>
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

      {/* Tabs */}
      <div className="flex items-center gap-1 mb-5 border-b border-border">
        {([
          { v: "settings", l: "Settings" },
          { v: "activity", l: "Activity" },
        ] as const).map((t) => (
          <button
            key={t.v}
            type="button"
            onClick={() => setActiveTab(t.v)}
            className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === t.v
                ? "border-accent text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.l}
          </button>
        ))}
      </div>

      {activeTab === "activity" && (
        <CameraActivityTab cameraId={cameraId} cameraName={camera.name} />
      )}

      {activeTab === "settings" && (
      <div className="space-y-5">
        {/* ── Quick personas ── */}
        <Section
          title="Personas"
          description="Apply a preset bundle to fill detection, recording, and summary settings in one click. Override anything afterward."
        >
          <PersonaPicker variant="compact" onApply={(patch) => applyPersona(patch)} />
        </Section>

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
                      : "Save bounded clips around AI observations with pre and post buffers. Best for rare, labelled triggers. Use Continuous if triggers fire constantly."}
            </p>
          </FieldRow>

          {recordingMode === "on_object" && (
            <FieldRow label="Record When Detected" hint="Which objects trigger recording. Labels come from the detection model.">
              <LabelPicker
                selected={recordingTriggerObjects}
                available={modelClasses}
                loading={modelClassesLoading}
                onChange={setRecordingTriggerObjects}
                placeholder="Search labels or press Enter for custom"
                activeModels={detectionModels.map((m) => m.model)}
                onAddModel={(model) => {
                  if (detectionModels.some((m) => m.model === model)) return;
                  setDetectionModels([
                    ...detectionModels,
                    { model, confidence: 0.35, enabled: true, label_filter: [] },
                  ]);
                }}
              />
              {recordingTriggerObjects.length === 0 && (
                <p className="text-[11px] text-muted-foreground mt-1.5">
                  No objects selected. Recording triggers on any detection.
                </p>
              )}
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

          <FieldRow label="Analysis Frequency" hint="Rate limit. Minimum gap between consecutive VLM calls">
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

          <FieldRow label="Trigger Condition" hint="Gate. What qualifies a frame for VLM analysis in the first place">
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
            <FieldRow label="Trigger Objects" hint="Labels come from the detection model. Type to search or add a custom label.">
              <LabelPicker
                selected={vlmTriggerObjects}
                available={modelClasses}
                loading={modelClassesLoading}
                onChange={setVlmTriggerObjects}
                placeholder="Search labels or press Enter for custom"
                activeModels={detectionModels.map((m) => m.model)}
                onAddModel={(model) => {
                  if (detectionModels.some((m) => m.model === model)) return;
                  setDetectionModels([
                    ...detectionModels,
                    { model, confidence: 0.35, enabled: true, label_filter: [] },
                  ]);
                }}
              />
              {vlmTriggerObjects.length === 0 && (
                <p className="text-[11px] text-muted-foreground mt-1.5">
                  No objects selected. VLM will trigger on any detection.
                </p>
              )}
            </FieldRow>
          )}

          <FieldRow label="Max Output Tokens" hint="Per-camera output cap. The provider's cap (set in Settings) further tightens this.">
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

          <FieldRow label="Max Input Tokens" hint="Per-camera prompt size cap. Empty defers to the provider's input cap.">
            <input
              type="number"
              min={64}
              value={vlmMaxInputTokens}
              onChange={(e) => setVlmMaxInputTokens(e.target.value)}
              className={inputClass}
              placeholder="defer to provider"
            />
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

        {/* Cascade refiner */}
        <Section
          title="Refiner (cascade)"
          description="Use a stronger second model only when triggers match. Cheap brain handles routine frames, smart brain handles the moments that matter."
        >
          <FieldRow label="Refiner Model" hint="Off when blank. Pick a different provider than AI Analysis.">
            <select
              value={vlmRefinerProviderId || ""}
              onChange={(e) => setVlmRefinerProviderId(e.target.value || null)}
              className={inputClass}
            >
              <option value="">Off</option>
              {providers
                .filter((p) => p.id !== vlmProviderId)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.default_model ? ` · ${p.default_model}` : ""}
                  </option>
                ))}
            </select>
            {vlmRefinerProviderId && vlmRefinerProviderId === vlmProviderId && (
              <p className="text-[11px] text-warning mt-1">
                Refiner must differ from the primary provider. Cascade
                disabled until you pick another model.
              </p>
            )}
          </FieldRow>

          {vlmRefinerProviderId && (
            <>
              <FieldRow label="Escalate when YOLO sees" hint="Detection labels that fire the refiner. Pet-cams, wildlife, vehicles all welcome.">
                <LabelPicker
                  selected={vlmRefinerTriggerObjects}
                  available={modelClasses}
                  loading={modelClassesLoading}
                  onChange={setVlmRefinerTriggerObjects}
                  placeholder="Search labels or press Enter for custom"
                  activeModels={detectionModels.map((m) => m.model)}
                  onAddModel={(model) => {
                    if (detectionModels.some((m) => m.model === model)) return;
                    setDetectionModels([
                      ...detectionModels,
                      { model, confidence: 0.35, enabled: true, label_filter: [] },
                    ]);
                  }}
                />
                {vlmRefinerTriggerObjects.length === 0 && vlmRefinerKeywords.length === 0 && (
                  <p className="text-[11px] text-warning mt-1.5">
                    No triggers set. Refiner will fire on every frame.
                    Add labels or keywords to gate it.
                  </p>
                )}
              </FieldRow>

              <FieldRow label="Escalate when primary mentions" hint="Comma or Enter to add. Case-insensitive substring match against the cheap model's text output.">
                <KeywordChipInput
                  values={vlmRefinerKeywords}
                  onChange={setVlmRefinerKeywords}
                  placeholder="package, delivery, stranger..."
                />
              </FieldRow>

              <FieldRow label="Refiner Max Output" hint="Per-camera output cap for the refiner. Empty defers to its provider cap.">
                <input
                  type="number"
                  min={50}
                  value={vlmRefinerMaxTokens}
                  onChange={(e) => setVlmRefinerMaxTokens(e.target.value)}
                  className={inputClass}
                  placeholder="defer to provider"
                />
              </FieldRow>

              <FieldRow label="Refiner Max Input" hint="Per-camera prompt size cap for the refiner. Empty defers to its provider cap.">
                <input
                  type="number"
                  min={64}
                  value={vlmRefinerMaxInputTokens}
                  onChange={(e) => setVlmRefinerMaxInputTokens(e.target.value)}
                  className={inputClass}
                  placeholder="defer to provider"
                />
              </FieldRow>
            </>
          )}
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
                      <DetectionModelSelect
                        value={m.model}
                        onChange={(v) => {
                          const updated = [...detectionModels];
                          updated[i] = { ...m, model: v };
                          setDetectionModels(updated);
                        }}
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
                      const used = new Set(detectionModels.map((x) => x.model));
                      const next = DETECTION_MODEL_CATALOG.find((m) => !used.has(m.value))?.value || "yolov8n.pt";
                      setDetectionModels([
                        ...detectionModels,
                        { model: next, confidence: 0.35, enabled: true, label_filter: [] },
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

        {/* ── Summarization ── */}
        <Section
          title="Summarization"
          description="Generate periodic or event-bound narrative recaps using a VLM. Summaries fuse per-frame descriptions, transcripts, and identity facts into a single story."
        >
          <FieldRow label="Mode" hint="Periodic fires on a fixed timer. Event opens on detection and closes after a quiet window. Both runs them independently.">
            <div className="flex gap-1.5">
              {(["off", "periodic", "event", "both"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setSummaryMode(m)}
                  className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors ${
                    summaryMode === m
                      ? "border-accent bg-accent/10 text-accent-foreground"
                      : "border-border hover:border-muted-foreground text-muted-foreground"
                  }`}
                >
                  {m === "off" ? "Off" : m === "periodic" ? "Periodic" : m === "event" ? "Event" : "Both"}
                </button>
              ))}
            </div>
          </FieldRow>

          {summaryMode !== "off" && (
            <>
              <FieldRow label="Summary Model" hint="Falls back to the AI Analysis provider, then the system default.">
                <select
                  value={summaryProviderId || ""}
                  onChange={(e) => setSummaryProviderId(e.target.value || null)}
                  className={inputClass}
                >
                  <option value="">
                    Use AI Analysis Provider{vlmProviderId ? "" : activeProvider ? ` (${activeProvider.name})` : ""}
                  </option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.default_model ? ` · ${p.default_model}` : ""}
                    </option>
                  ))}
                </select>
              </FieldRow>

              <FieldRow label="Max Output Tokens" hint="Cap on summary length. 400 fits a 2-4 sentence recap comfortably.">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={100}
                    max={1500}
                    step={50}
                    value={summaryMaxTokens}
                    onChange={(e) => setSummaryMaxTokens(Number(e.target.value))}
                    className="flex-1 accent-accent"
                  />
                  <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                    {summaryMaxTokens} tok
                  </span>
                </div>
              </FieldRow>
            </>
          )}

          {(summaryMode === "periodic" || summaryMode === "both") && (
            <FieldRow label="Period" hint="How often a periodic summary fires. The first one anchors when summarization is enabled, not retroactively.">
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={300}
                  max={14400}
                  step={300}
                  value={summaryPeriodSeconds}
                  onChange={(e) => setSummaryPeriodSeconds(Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                  {formatInterval(summaryPeriodSeconds)}
                </span>
              </div>
            </FieldRow>
          )}

          {(summaryMode === "event" || summaryMode === "both") && (
            <>
              <FieldRow label="Event Trigger Objects" hint="Detection labels that count as activity. Default is person. Override for pet-cams, wildlife, vehicles.">
                <LabelPicker
                  selected={summaryEventTriggerObjects}
                  available={modelClasses}
                  loading={modelClassesLoading}
                  onChange={setSummaryEventTriggerObjects}
                  placeholder="Search labels or press Enter for custom"
                  activeModels={detectionModels.map((m) => m.model)}
                  onAddModel={(model) => {
                    if (detectionModels.some((m) => m.model === model)) return;
                    setDetectionModels([
                      ...detectionModels,
                      { model, confidence: 0.35, enabled: true, label_filter: [] },
                    ]);
                  }}
                />
                {summaryEventTriggerObjects.length === 0 && (
                  <p className="text-[11px] text-warning mt-1.5">
                    No labels selected. Event mode will never fire.
                  </p>
                )}
              </FieldRow>

              <FieldRow label="Quiet Window" hint="Seconds without a matching detection before the event closes and gets summarized.">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={10}
                    max={600}
                    step={5}
                    value={summaryEventQuietSeconds}
                    onChange={(e) => setSummaryEventQuietSeconds(Number(e.target.value))}
                    className="flex-1 accent-accent"
                  />
                  <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                    {formatInterval(summaryEventQuietSeconds)}
                  </span>
                </div>
              </FieldRow>

              <FieldRow label="Minimum Duration" hint="Drop events shorter than this. Filters out flickers like a bird flying through.">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={120}
                    step={1}
                    value={summaryEventMinDurationSeconds}
                    onChange={(e) => setSummaryEventMinDurationSeconds(Number(e.target.value))}
                    className="flex-1 accent-accent"
                  />
                  <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                    {formatInterval(summaryEventMinDurationSeconds)}
                  </span>
                </div>
              </FieldRow>
            </>
          )}
        </Section>

        {/* ── Audio Conversations ── */}
        <Section
          title="Audio Conversations"
          description="Group consecutive transcripts into a single rolling card and summarize the conversation when it goes quiet."
        >
          <FieldRow label="Conversation Gap" hint="Maximum silence between transcripts that still counts as the same conversation. Beyond this, a new conversation opens.">
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={5}
                max={300}
                step={5}
                value={conversationGapSeconds}
                onChange={(e) => setConversationGapSeconds(Number(e.target.value))}
                className="flex-1 accent-accent"
              />
              <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                {formatInterval(conversationGapSeconds)}
              </span>
            </div>
          </FieldRow>

          <FieldRow label="Generate Summary" hint="When the conversation closes, send the full transcript to the summary VLM and replace the live caption with a one-line recap.">
            <Toggle
              checked={conversationSummaryEnabled}
              onChange={setConversationSummaryEnabled}
              label={conversationSummaryEnabled ? "Enabled" : "Disabled"}
            />
          </FieldRow>

          {conversationSummaryEnabled && (
            <FieldRow label="Minimum Messages" hint="Skip the summary call for short conversations (one-liners) to save tokens.">
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={1}
                  max={10}
                  step={1}
                  value={conversationMinMessages}
                  onChange={(e) => setConversationMinMessages(Number(e.target.value))}
                  className="flex-1 accent-accent"
                />
                <span className="font-mono text-xs text-muted-foreground w-20 text-right">
                  {conversationMinMessages} msg
                </span>
              </div>
            </FieldRow>
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
          title="Zones and Tripwires"
          description="Include and exclude masks for motion, loiter zones, and tripwires for line-crossing rules."
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
      )}

      {/* Sticky save bar. only on settings tab. */}
      {activeTab === "settings" && (
      <div className="sticky bottom-0 mt-6 -mx-6 px-6 py-3 bg-background/80 backdrop-blur-sm border-t border-border flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs">
          {error && <span className="text-danger">{error}</span>}
          {!error && saving && (
            <>
              <svg
                className="animate-spin w-3 h-3 text-muted-foreground"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeDasharray="40 60"
                />
              </svg>
              <span className="text-muted-foreground">Saving.</span>
            </>
          )}
          {!error && !saving && saved && (
            <span className="text-accent">All changes saved</span>
          )}
          {!error && !saving && !saved && (
            <span className="text-muted-foreground/70">
              Changes save automatically
            </span>
          )}
        </div>
        <Link
          href="/"
          className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
        >
          Done
        </Link>
      </div>
      )}
    </div>
  );
}

interface ActivityConversation {
  id: string;
  camera_id: string;
  started_at: string;
  ended_at_provisional: string;
  ended_at: string | null;
  finalized: boolean;
  transcript_count: number;
  summary_text: string | null;
  cleaned_text: string | null;
  summary_provider_name: string | null;
  has_clip?: boolean;
}

interface ActivitySummary {
  id: string;
  camera_id: string;
  kind: string;
  started_at: string;
  ended_at: string;
  provider_name: string | null;
  trigger_reason: string;
  summary_text: string;
  people_seen: { name: string; sightings: number; first_seen?: string; last_seen?: string }[] | null;
  plates_seen: string[] | null;
  object_counts: Record<string, number> | null;
}

/**
 * Per-camera activity feed. Pulls /api/conversations and /api/summaries
 * scoped to this camera and renders them interleaved by recency. Uses
 * the same cards as the dashboard so a user can navigate from a tile
 * straight to the focused per-camera view without learning a new
 * layout.
 */
function CameraActivityTab({
  cameraId,
  cameraName,
}: {
  cameraId: string;
  cameraName: string;
}) {
  const { authFetch } = useAuth();
  const [convs, setConvs] = useState<ActivityConversation[]>([]);
  const [summaries, setSummaries] = useState<ActivitySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "conversations" | "summaries">("all");

  const refresh = useCallback(async () => {
    try {
      const [cR, sR] = await Promise.all([
        authFetch(`/api/conversations?camera_id=${cameraId}&limit=50`),
        authFetch(`/api/summaries?camera_id=${cameraId}&limit=50`),
      ]);
      if (cR.ok) setConvs(await cR.json());
      if (sR.ok) setSummaries(await sR.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch, cameraId]);

  useEffect(() => {
    refresh();
    const i = setInterval(refresh, 15000);
    return () => clearInterval(i);
  }, [refresh]);

  type Entry =
    | { kind: "conversation"; ts: number; data: ActivityConversation }
    | { kind: "summary"; ts: number; data: ActivitySummary };

  const entries: Entry[] = [];
  if (filter === "all" || filter === "conversations") {
    for (const c of convs) {
      entries.push({
        kind: "conversation",
        ts: new Date(c.ended_at_provisional).getTime(),
        data: c,
      });
    }
  }
  if (filter === "all" || filter === "summaries") {
    for (const s of summaries) {
      entries.push({
        kind: "summary",
        ts: new Date(s.ended_at).getTime(),
        data: s,
      });
    }
  }
  entries.sort((a, b) => b.ts - a.ts);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {([
          { v: "all", l: `All (${convs.length + summaries.length})` },
          { v: "conversations", l: `Conversations (${convs.length})` },
          { v: "summaries", l: `Summaries (${summaries.length})` },
        ] as const).map((f) => (
          <button
            key={f.v}
            type="button"
            onClick={() => setFilter(f.v)}
            className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
              filter === f.v
                ? "border-accent bg-accent/10 text-accent-foreground"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {f.l}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-muted-foreground/70 font-mono">
          refreshes every 15s
        </span>
      </div>

      {loading ? (
        <div className="text-xs text-muted-foreground">Loading activity.</div>
      ) : entries.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card/30 p-8 text-center">
          <h3 className="text-sm font-medium mb-1">Nothing yet</h3>
          <p className="text-xs text-muted-foreground max-w-sm mx-auto">
            Activity rolls up here as conversations close and the
            summarizer worker runs. Check the dashboard timeline for the
            latest live signal.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5">
          {entries.map((e) => {
            if (e.kind === "conversation") {
              const c = e.data;
              return (
                <ConversationCard
                  key={`c-${c.id}`}
                  id={c.id}
                  cameraId={c.camera_id}
                  cameraName={cameraName}
                  startedAt={c.started_at}
                  endedAtProvisional={c.ended_at_provisional}
                  endedAt={c.ended_at}
                  finalized={c.finalized}
                  transcriptCount={c.transcript_count}
                  summaryText={c.summary_text}
                  cleanedText={c.cleaned_text}
                  summaryProviderName={c.summary_provider_name}
                  hasClip={c.has_clip}
                />
              );
            }
            const s = e.data;
            return (
              <SummaryCard
                key={`s-${s.id}`}
                id={s.id}
                cameraId={s.camera_id}
                cameraName={cameraName}
                kind={s.kind}
                startedAt={s.started_at}
                endedAt={s.ended_at}
                providerName={s.provider_name}
                triggerReason={s.trigger_reason}
                summaryText={s.summary_text}
                peopleSeen={s.people_seen}
                platesSeen={s.plates_seen}
                objectCounts={s.object_counts}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
