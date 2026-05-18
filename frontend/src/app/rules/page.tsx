"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";

const WEBRTC_URL =
  process.env.NEXT_PUBLIC_WEBRTC_URL || "http://localhost:8889";

function extractStreamName(streamUrl: string): string {
  try {
    const path = streamUrl.replace(/\/+$/, "");
    const lastSlash = path.lastIndexOf("/");
    return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
  } catch {
    return streamUrl;
  }
}

interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  trigger_pattern: Record<string, unknown>;
  conditions: Record<string, unknown> | null;
  actions: Record<string, unknown> | Record<string, unknown>[];
  cooldown_seconds: number;
  created_at: string;
}

interface EventEntry {
  id: string;
  rule_id: string | null;
  observation_id: string | null;
  fired_at: string;
  payload: Record<string, unknown> | null;
  acknowledged_at: string | null;
  action_status: string;
  action_error: string | null;
  action_type: string | null;
}

interface Camera {
  id: string;
  name: string;
  status: string;
  stream_url?: string;
  width?: number;
  height?: number;
  detection_models?: { model: string; enabled?: boolean }[] | null;
}

interface TriggerType {
  value: string;
  label: string;
  icon: (props: { className?: string }) => React.ReactElement;
  desc: string;
  accent: string;  // tailwind color root (green, blue, amber, indigo, rose, slate)
  group: "vision" | "faces" | "motion" | "audio" | "spatial" | "any";
}

// Minimal inline SVGs. 18px, stroke 1.75, currentColor.
const Icon = {
  box: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
      <path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>
    </svg>
  ),
  user: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
  ),
  userCheck: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m16 11 2 2 4-4"/>
    </svg>
  ),
  userQ: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
      <path d="M17 11a2 2 0 1 1 3 1.7c-.4.3-1 .6-1 1.3"/><path d="M19 17h.01"/>
    </svg>
  ),
  wave: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12c2 0 2-3 4-3s2 6 4 6 2-9 4-9 2 9 4 9 2-3 4-3"/>
    </svg>
  ),
  speaker: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    </svg>
  ),
  clock: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
    </svg>
  ),
  tripwire: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17 21 7"/><path d="m17 5 4 2-2 4"/><circle cx="6" cy="18" r="1.5"/>
    </svg>
  ),
  spark: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v18"/><path d="M3 12h18"/><path d="m5.6 5.6 12.8 12.8"/><path d="m18.4 5.6-12.8 12.8"/>
    </svg>
  ),
};

const TRIGGER_TYPES: TriggerType[] = [
  { value: "object_detected", label: "Object detected", icon: Icon.box,       desc: "Person, car, dog, package, or any YOLO class.", accent: "green",  group: "vision" },
  { value: "face_detected",   label: "Face detected",   icon: Icon.user,      desc: "Any face visible in frame, known or not.",       accent: "blue",   group: "faces" },
  { value: "face_recognized", label: "Known face",      icon: Icon.userCheck, desc: "A specific person in your library.",              accent: "blue",   group: "faces" },
  { value: "face_unknown",    label: "Unknown face",    icon: Icon.userQ,     desc: "Someone not yet matched to a person.",            accent: "amber",  group: "faces" },
  { value: "motion",          label: "Motion",          icon: Icon.wave,      desc: "Pixel-level movement above a threshold.",         accent: "slate",  group: "motion" },
  { value: "audio_event",     label: "Audio event",     icon: Icon.speaker,   desc: "Baby cry, scream, glass, alarm, bark, gunshot.",  accent: "rose",   group: "audio" },
  { value: "clap_pattern",    label: "Clap pattern",    icon: Icon.speaker,   desc: "Two, three, or more claps in a row.",             accent: "rose",   group: "audio" },
  { value: "speech_phrase",   label: "Spoken phrase",   icon: Icon.speaker,   desc: "Fire when a phrase is said near a camera.",       accent: "rose",   group: "audio" },
  { value: "loitering",       label: "Loitering",       icon: Icon.clock,     desc: "Someone stays inside a zone too long.",           accent: "amber",  group: "spatial" },
  { value: "line_cross",      label: "Tripwire",        icon: Icon.tripwire,  desc: "A tracked object crosses a line.",                accent: "indigo", group: "spatial" },
  { value: "any",             label: "Any observation", icon: Icon.spark,     desc: "Fire on every processed keyframe.",               accent: "slate",  group: "any" },
];

const TRIGGER_ACCENTS: Record<string, { active: string; dot: string }> = {
  green:  { active: "border-green-500 bg-green-500/10 ring-green-500/40",  dot: "bg-green-500" },
  blue:   { active: "border-sky-500 bg-sky-500/10 ring-sky-500/40",        dot: "bg-sky-500" },
  amber:  { active: "border-amber-500 bg-amber-500/10 ring-amber-500/40",  dot: "bg-amber-500" },
  rose:   { active: "border-rose-500 bg-rose-500/10 ring-rose-500/40",     dot: "bg-rose-500" },
  indigo: { active: "border-indigo-500 bg-indigo-500/10 ring-indigo-500/40", dot: "bg-indigo-500" },
  slate:  { active: "border-slate-400 bg-slate-400/10 ring-slate-400/40",  dot: "bg-slate-400" },
};

const AUDIO_LABELS = [
  { value: "baby_cry", label: "Baby cry" },
  { value: "crying", label: "Crying / sobbing" },
  { value: "scream", label: "Scream / shout" },
  { value: "speech", label: "Speech" },
  { value: "glass_break", label: "Glass break" },
  { value: "alarm", label: "Alarm / siren" },
  { value: "bark", label: "Dog bark" },
  { value: "gunshot", label: "Gunshot / explosion" },
];

const OBJECT_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle",
  "dog", "cat", "bird", "backpack", "handbag",
  "suitcase", "umbrella",
];

const ACTION_TYPES = [
  { value: "webhook", label: "Webhook" },
  { value: "api_call", label: "API Call" },
  { value: "broadcast", label: "WebSocket broadcast" },
  { value: "notify", label: "Notification" },
  { value: "email", label: "Email" },
  { value: "telegram", label: "Telegram" },
  { value: "vlm_call", label: "VLM Call" },
];

// Variables available in Telegram message templates. Kept in sync
// with TELEGRAM_TEMPLATE_VARS in services/events/actions.py.
const TELEGRAM_TEMPLATE_VARS = [
  { key: "rule_name", desc: "Name of the rule that fired" },
  { key: "camera_name", desc: "Camera that produced the observation" },
  { key: "timestamp_local", desc: "Time of the observation in the camera's timezone" },
  { key: "vlm_description", desc: "Scene description from the VLM, if any" },
  { key: "detections_summary", desc: "Compact list of objects and faces detected" },
  { key: "observation_id", desc: "Database id of the observation" },
  { key: "event_id", desc: "Database id of the fired event" },
];

interface TelegramChannelOption {
  id: string;
  label: string;
  bot_username: string | null;
  chat_title: string | null;
  enabled: boolean;
  pairing_status: string;
}

const VLM_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "ollama", label: "Ollama" },
];

const VLM_SCHEMA_PRESETS: Record<string, string> = {
  threat: JSON.stringify(
    {
      type: "object",
      properties: {
        level: { type: "string", enum: ["low", "medium", "high"] },
        reason: { type: "string" },
      },
      required: ["level", "reason"],
    },
    null,
    2,
  ),
  notify: JSON.stringify(
    {
      type: "object",
      properties: {
        notify: { type: "boolean" },
        reason: { type: "string" },
      },
      required: ["notify", "reason"],
    },
    null,
    2,
  ),
  intent: JSON.stringify(
    {
      type: "object",
      properties: {
        intent: { type: "string", enum: ["delivery", "visitor", "intruder", "unknown"] },
        confidence: { type: "number" },
      },
      required: ["intent", "confidence"],
    },
    null,
    2,
  ),
  entities: JSON.stringify(
    {
      type: "object",
      properties: {
        people: { type: "integer" },
        vehicles: { type: "integer" },
        animals: { type: "integer" },
      },
      required: ["people", "vehicles", "animals"],
    },
    null,
    2,
  ),
};

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

const AUTH_TYPES = [
  { value: "none", label: "No auth" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key header" },
  { value: "basic", label: "Basic auth" },
];

const TEMPLATE_VARIABLES = [
  { key: "event_id", desc: "Event UUID" },
  { key: "rule_name", desc: "Rule name" },
  { key: "camera_id", desc: "Camera UUID" },
  { key: "timestamp", desc: "ISO timestamp" },
  { key: "motion_score", desc: "Motion score (0-1)" },
  { key: "object_detections", desc: "Detection results (object)" },
  { key: "person_detections", desc: "Face results (object)" },
  { key: "vlm_description", desc: "VLM scene description" },
  { key: "confidence", desc: "VLM confidence" },
  { key: "observation_id", desc: "Observation UUID" },
];

const DEFAULT_PAYLOAD_TEMPLATE = `{
  "event": "{{rule_name}}",
  "camera": "{{camera_id}}",
  "timestamp": "{{timestamp}}",
  "description": "{{vlm_description}}",
  "detections": "{{object_detections}}"
}`;

// Populated by the component so describeTrigger can resolve ids to names.
const personLookup = new Map<string, string>();
const cameraLookup = new Map<string, string>();

function describeTrigger(pattern: Record<string, unknown>): string {
  const t = pattern.type as string;
  if (t === "object_detected") {
    const label = pattern.label as string | undefined;
    return label ? `When "${label}" detected` : "When any object detected";
  }
  if (t === "face_detected") return "When any face detected";
  if (t === "face_recognized") {
    const pid = pattern.person_id as string | undefined;
    if (!pid) return "When any known face recognized";
    const person = personLookup.get(pid);
    return person ? `When ${person} recognized` : `When person ${pid.slice(0, 8)} recognized`;
  }
  if (t === "face_unknown") return "When unknown face detected";
  if (t === "motion") {
    const ms = pattern.min_score as number | undefined;
    return ms ? `When motion score >= ${ms}` : "When motion detected";
  }
  if (t === "audio_event") {
    const label = pattern.label as string | undefined;
    const match = AUDIO_LABELS.find((a) => a.value === label);
    return label ? `When ${match?.label || label} heard` : "When any audio event";
  }
  if (t === "loitering") {
    const cid = pattern.camera_id as string | undefined;
    const zone = pattern.zone_name as string | undefined;
    const secs = pattern.threshold_seconds as number | undefined;
    const who = pattern.label as string | undefined;
    const subject = who ? `a ${who}` : "someone";
    const secText = secs ? ` > ${secs}s` : "";
    if (cid) {
      const camName = cameraLookup.get(cid) || cid.slice(0, 8);
      return `When ${subject} loiters on ${camName}${secText}`;
    }
    if (zone) return `When ${subject} loiters in "${zone}"${secText}`;
    return `When ${subject} loiters${secText}`;
  }
  if (t === "line_cross") {
    const cid = pattern.camera_id as string | undefined;
    const zone = pattern.zone_name as string | undefined;
    const dir = pattern.direction as string | undefined;
    const who = pattern.label as string | undefined;
    const dirText = dir && dir !== "any" ? ` (${dir})` : "";
    const subject = who ? `a ${who}` : "a tracked object";
    if (cid) {
      const camName = cameraLookup.get(cid) || cid.slice(0, 8);
      return `When ${subject} crosses tripwire on ${camName}${dirText}`;
    }
    if (zone) return `When tripwire "${zone}" crossed${dirText}`;
    return `When any tripwire crossed${dirText}`;
  }
  if (t === "any") return "On every observation";
  return "Unknown trigger";
}

function describeActions(actions: Record<string, unknown> | Record<string, unknown>[]): string {
  const list = Array.isArray(actions) ? actions : [actions];
  return list
    .map((a) => {
      if (a.type === "webhook") {
        const hasAuth = !!(a.auth as Record<string, unknown> | undefined);
        return `POST to ${(a.url as string) || "..."}${hasAuth ? " (authenticated)" : ""}`;
      }
      if (a.type === "api_call") {
        const method = (a.method as string) || "POST";
        const hasAuth = !!(a.auth as Record<string, unknown> | undefined);
        return `${method} ${(a.url as string) || "..."}${hasAuth ? " (authenticated)" : ""}`;
      }
      if (a.type === "broadcast") return "Broadcast via WebSocket";
      if (a.type === "notify") return `Notify. "${(a.message as string) || "..."}"`;
      if (a.type === "email") return `Email to ${(a.to as string) || "..."}`;
      if (a.type === "telegram") {
        const cid = (a.channel_id as string) || "";
        return cid ? `Telegram via channel ${cid.slice(0, 8)}` : "Telegram (no channel selected)";
      }
      return String(a.type);
    })
    .join(", ");
}

function formatCooldown(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${seconds} seconds`;
  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "1 minute";
  return `${minutes} minutes`;
}

function resolveCameraNames(camIds: string[], cameras: Camera[]): string {
  if (camIds.length === 0) return "any camera";
  const names = camIds.map((cid) => {
    const cam = cameras.find((c) => c.id === cid);
    return cam ? cam.name : cid.slice(0, 8);
  });
  return names.join(", ");
}

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"];
const WEEKEND = ["sat", "sun"];

function describeSchedule(days: string[] | undefined, timeAfter: string | undefined, timeBefore: string | undefined): string {
  const scheduleParts: string[] = [];
  if (days && days.length > 0 && days.length < 7) {
    const isWeekdays = WEEKDAYS.every((d) => days.includes(d)) && days.length === 5;
    const isWeekend = WEEKEND.every((d) => days.includes(d)) && days.length === 2;
    if (isWeekdays) scheduleParts.push("on weekdays");
    else if (isWeekend) scheduleParts.push("on weekends");
    else scheduleParts.push(`on ${days.map((d) => d.charAt(0).toUpperCase() + d.slice(1)).join(", ")}`);
  }
  if (timeAfter || timeBefore) {
    scheduleParts.push(`between ${timeAfter || "00:00"} and ${timeBefore || "23:59"}`);
  }
  return scheduleParts.join(" ");
}

function composeSummary(
  trigger: string,
  cameraLabel: string,
  schedule: string,
  actionLabel: string,
  cooldownSeconds: number,
): string {
  const parts = [trigger, `on ${cameraLabel}`];
  if (schedule) parts.push(schedule);
  parts.push(actionLabel);
  let sentence = parts.join(", ") + ".";
  if (cooldownSeconds > 0) {
    sentence += ` Cooldown. ${formatCooldown(cooldownSeconds)}.`;
  }
  return sentence;
}

function buildRuleSummary(rule: Rule, cameras: Camera[]): string {
  const cond = rule.conditions || {};
  const camIds = (cond.camera_ids as string[]) || (cond.camera_id ? [cond.camera_id as string] : []);
  return composeSummary(
    describeTrigger(rule.trigger_pattern),
    resolveCameraNames(camIds, cameras),
    describeSchedule(cond.days as string[] | undefined, cond.time_after as string | undefined, cond.time_before as string | undefined),
    describeActions(rule.actions),
    rule.cooldown_seconds,
  );
}

function SummaryCard({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`bg-blue-500/10 border border-blue-500/20 rounded-lg text-sm text-zinc-200 flex gap-3 items-start ${className || "p-4"}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="text-blue-400 flex-shrink-0 mt-0.5">
        <path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.4 1 .8 1 1.3v2h6v-2c0-.5.4-.9 1-1.3A7 7 0 0 0 12 2z"/>
      </svg>
      <span>{text}</span>
    </div>
  );
}

interface SelectOption { value: string; label: string; hint?: string }
function StyledSelect({
  value,
  options,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  options: SelectOption[];
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} className={`relative ${className || ""}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 rounded-md bg-background border border-border text-sm hover:border-muted-foreground/40 focus:outline-none focus:border-accent transition-colors"
      >
        <span className={current ? "" : "text-muted-foreground"}>
          {current?.label || placeholder || "Select."}
        </span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}>
          <path d="m6 9 6 6 6-6"/>
        </svg>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-border bg-card shadow-lg max-h-64 overflow-y-auto py-1">
          {options.map((o) => {
            const selected = o.value === value;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => { onChange(o.value); setOpen(false); }}
                className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between gap-2 hover:bg-muted/60 ${selected ? "bg-muted/40" : ""}`}
              >
                <span className="min-w-0">
                  <span className="block truncate">{o.label}</span>
                  {o.hint && <span className="block text-[10px] text-muted-foreground truncate">{o.hint}</span>}
                </span>
                {selected && (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-accent flex-shrink-0">
                    <path d="M20 6 9 17l-5-5"/>
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function GeometryEditor({
  camera,
  mode,
  points,
  onChange,
}: {
  camera: Camera;
  mode: "line" | "polygon";
  points: number[][];
  onChange: (pts: number[][]) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 640, h: 360 });
  const frameW = camera.width || 1280;
  const frameH = camera.height || 720;

  // Size canvas to container width, keep camera aspect.
  useEffect(() => {
    const update = () => {
      const w = wrapRef.current?.clientWidth || 640;
      const h = Math.round((w * frameH) / frameW);
      setSize({ w, h });
    };
    update();
    const ro = new ResizeObserver(update);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [frameW, frameH]);

  const scaleX = size.w / frameW;
  const scaleY = size.h / frameH;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, size.w, size.h);
    if (points.length === 0) return;

    const stroke = mode === "line" ? "#818cf8" : "#fbbf24";
    const fill = mode === "line" ? "rgba(129,140,248,0.15)" : "rgba(251,191,36,0.18)";

    ctx.beginPath();
    ctx.moveTo(points[0][0] * scaleX, points[0][1] * scaleY);
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(points[i][0] * scaleX, points[i][1] * scaleY);
    }
    if (mode === "polygon" && points.length >= 3) {
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
    }
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2.5;
    ctx.stroke();

    for (const p of points) {
      ctx.beginPath();
      ctx.arc(p[0] * scaleX, p[1] * scaleY, 5, 0, Math.PI * 2);
      ctx.fillStyle = stroke;
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }, [points, size, scaleX, scaleY, mode]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = Math.round((e.clientX - rect.left) / scaleX);
    const y = Math.round((e.clientY - rect.top) / scaleY);
    if (mode === "line") {
      if (points.length >= 2) {
        onChange([[x, y]]);
      } else {
        onChange([...points, [x, y]]);
      }
    } else {
      onChange([...points, [x, y]]);
    }
  };

  const streamName = camera.stream_url ? extractStreamName(camera.stream_url) : "";
  const iframeSrc = streamName ? `${WEBRTC_URL}/${streamName}/` : "";

  return (
    <div className="space-y-2">
      <div ref={wrapRef} className="relative w-full bg-black rounded-md overflow-hidden border border-border" style={{ height: size.h }}>
        {iframeSrc && camera.status !== "offline" ? (
          <iframe
            src={iframeSrc}
            className="absolute inset-0 w-full h-full border-0 pointer-events-none"
            allow="autoplay; encrypted-media"
            sandbox="allow-scripts allow-same-origin"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            {camera.status === "offline" ? "Camera offline" : "No preview"}
          </div>
        )}
        <canvas
          ref={canvasRef}
          width={size.w}
          height={size.h}
          onClick={handleClick}
          className="absolute inset-0 w-full h-full cursor-crosshair"
        />
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {mode === "line"
            ? points.length < 2
              ? `Click two points to place a tripwire. (${points.length}/2)`
              : "Tripwire placed. Click again to redraw."
            : points.length < 3
              ? `Click to add polygon points. (${points.length}/≥3)`
              : `${points.length} points. Add more or clear to redraw.`}
        </span>
        {points.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="px-2 py-0.5 rounded border border-border hover:bg-muted transition-colors"
          >Clear</button>
        )}
      </div>
    </div>
  );
}

function ModelClassPicker({
  value,
  onChange,
  activeModels,
  classes,
  loading,
  anyLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  activeModels: string[];
  classes: string[];
  loading: boolean;
  anyLabel: string;
}) {
  const needsModel = activeModels.length === 0;
  const options = [
    { value: "", label: anyLabel },
    ...classes.map((l) => ({ value: l, label: l })),
  ];

  return (
    <div className="space-y-2">
      {activeModels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          <span className="text-[10px] text-muted-foreground self-center">Labels sourced from.</span>
          {activeModels.map((m) => (
            <span key={m} className="px-1.5 py-0.5 text-[10px] font-mono rounded border border-border bg-muted/30 text-muted-foreground">
              {m}
            </span>
          ))}
        </div>
      )}
      {needsModel ? (
        <div className="rounded-md border border-dashed border-amber-500/40 bg-amber-500/5 p-2.5 text-[11px] text-amber-300">
          No detection model configured on the selected camera(s). Add one on the{" "}
          <a href="/cameras" className="underline hover:text-amber-200">camera settings</a> page first. Labels come from whichever model you pick.
        </div>
      ) : loading ? (
        <p className="text-[11px] text-muted-foreground">Loading labels from model.</p>
      ) : classes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          Model loaded no classes. First-run download may still be running. Refresh in a moment.
        </p>
      ) : (
        <StyledSelect value={value} options={options} onChange={onChange} />
      )}
    </div>
  );
}

export default function RulesPage() {
  const { authFetch } = useAuth();
  const [rules, setRules] = useState<Rule[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [persons, setPersons] = useState<{ id: string; display_name: string; relationship: string | null; photo_path: string | null }[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editRule, setEditRule] = useState<Rule | null>(null);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);
  const [ruleEvents, setRuleEvents] = useState<EventEntry[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  // Form state
  const [formName, setFormName] = useState("");
  const [formEnabled, setFormEnabled] = useState(true);
  const [formTriggerType, setFormTriggerType] = useState("object_detected");
  const [formTriggerLabel, setFormTriggerLabel] = useState("");
  const [formTriggerPersonId, setFormTriggerPersonId] = useState("");
  const [formTriggerSensitivity, setFormTriggerSensitivity] = useState("medium");
  const [formTriggerAudioLabel, setFormTriggerAudioLabel] = useState("baby_cry");
  const [formTriggerAudioMinScore, setFormTriggerAudioMinScore] = useState("0.35");
  const [formTriggerLineDirection, setFormTriggerLineDirection] = useState("any");
  const [formTriggerGeomCamId, setFormTriggerGeomCamId] = useState("");
  const [formTriggerGeomPoints, setFormTriggerGeomPoints] = useState<number[][]>([]);
  const [formTriggerLoiterSeconds, setFormTriggerLoiterSeconds] = useState("30");
  const [formTriggerObjectClass, setFormTriggerObjectClass] = useState("");
  const [formTriggerClapCount, setFormTriggerClapCount] = useState("2");
  const [formTriggerPhrases, setFormTriggerPhrases] = useState<string[]>([]);
  const [formTriggerPhraseMatch, setFormTriggerPhraseMatch] = useState<"any" | "all">("any");
  const [formCondCameras, setFormCondCameras] = useState<string[]>([]);
  const [formScheduleMode, setFormScheduleMode] = useState<"always" | "custom">("always");
  const [formCondDays, setFormCondDays] = useState<string[]>([]);
  const [formCondTimeAfter, setFormCondTimeAfter] = useState("");
  const [formCondTimeBefore, setFormCondTimeBefore] = useState("");
  const [formCondConfidence, setFormCondConfidence] = useState("any");
  const [formActionType, setFormActionType] = useState("notify");
  const [formActionUrl, setFormActionUrl] = useState("");
  const [formActionMethod, setFormActionMethod] = useState("POST");
  const [formActionMessage, setFormActionMessage] = useState("");
  const [formActionSeverity, setFormActionSeverity] = useState("info");
  const [formActionAuthType, setFormActionAuthType] = useState("none");
  const [formActionAuthToken, setFormActionAuthToken] = useState("");
  const [formActionAuthHeader, setFormActionAuthHeader] = useState("X-API-Key");
  const [formActionAuthKey, setFormActionAuthKey] = useState("");
  const [formActionAuthUser, setFormActionAuthUser] = useState("");
  const [formActionAuthPass, setFormActionAuthPass] = useState("");
  const [formActionPayloadTemplate, setFormActionPayloadTemplate] = useState("");
  const [formActionUseCustomPayload, setFormActionUseCustomPayload] = useState(false);
  const [formPayloadError, setFormPayloadError] = useState("");
  const [formActionEmailTo, setFormActionEmailTo] = useState("");
  const [formActionEmailSubject, setFormActionEmailSubject] = useState("");
  const [formActionEmailBody, setFormActionEmailBody] = useState("");

  // Telegram action fields
  const [telegramChannels, setTelegramChannels] = useState<TelegramChannelOption[]>([]);
  const [telegramChannelsLoading, setTelegramChannelsLoading] = useState(false);
  const [formActionTelegramChannelId, setFormActionTelegramChannelId] = useState("");
  const [formActionTelegramTemplate, setFormActionTelegramTemplate] = useState(
    "<b>{rule_name}</b> on {camera_name}\n{vlm_description}\n{detections_summary}",
  );
  const [formActionTelegramSilent, setFormActionTelegramSilent] = useState(false);
  const [formActionTelegramThumbnail, setFormActionTelegramThumbnail] = useState(false);

  // VLM call action fields
  const [formVlmProvider, setFormVlmProvider] = useState("openai");
  const [formVlmModel, setFormVlmModel] = useState("gpt-4o-mini");
  const [formVlmSystem, setFormVlmSystem] = useState("{{defaults.system}}");
  const [formVlmPrompt, setFormVlmPrompt] = useState(
    "Describe the scene. Focus on people, vehicles, and unusual activity.",
  );
  const [formVlmAttachImage, setFormVlmAttachImage] = useState(true);
  const [formVlmUseSchema, setFormVlmUseSchema] = useState(false);
  const [formVlmSchemaText, setFormVlmSchemaText] = useState(VLM_SCHEMA_PRESETS.threat);
  const [formVlmOutput, setFormVlmOutput] = useState("result");
  const [formVlmMaxRetries, setFormVlmMaxRetries] = useState("1");
  const [formVlmOnError, setFormVlmOnError] = useState("continue");
  const [formVlmTimeoutMs, setFormVlmTimeoutMs] = useState("20000");
  const [formCooldown, setFormCooldown] = useState("300");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Dynamic class vocabulary from configured detection models.
  const [modelClasses, setModelClasses] = useState<string[]>([]);
  const [modelClassesLoading, setModelClassesLoading] = useState(false);
  const activeModels = useMemo(() => {
    const scoped = formCondCameras.length > 0
      ? cameras.filter((c) => formCondCameras.includes(c.id))
      : cameras;
    const set = new Set<string>();
    for (const c of scoped) {
      for (const m of c.detection_models || []) {
        if (m?.model && m.enabled !== false) set.add(m.model);
      }
    }
    return Array.from(set).sort();
  }, [cameras, formCondCameras]);

  useEffect(() => {
    if (activeModels.length === 0) {
      setModelClasses([]);
      return;
    }
    let cancelled = false;
    setModelClassesLoading(true);
    const params = activeModels.map((m) => `model=${encodeURIComponent(m)}`).join("&");
    authFetch(`/api/detection-models/classes?${params}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data?.classes) setModelClasses(data.classes);
      })
      .catch(() => { /* silent */ })
      .finally(() => { if (!cancelled) setModelClassesLoading(false); });
    return () => { cancelled = true; };
  }, [activeModels, authFetch]);

  const fetchRules = useCallback(async () => {
    try {
      const res = await authFetch("/api/rules");
      if (res.ok) setRules(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) {
        const list = await res.json();
        setCameras(list);
        cameraLookup.clear();
        for (const c of list) cameraLookup.set(c.id, c.name);
      }
    } catch {
      /* silent */
    }
  }, []);

  const fetchPersons = useCallback(async () => {
    try {
      const res = await authFetch("/api/persons");
      if (res.ok) {
        const list = await res.json();
        setPersons(list);
        personLookup.clear();
        for (const p of list) personLookup.set(p.id, p.display_name);
      }
    } catch {
      /* silent */
    }
  }, []);

  const fetchRuleEvents = useCallback(async (ruleId: string) => {
    setEventsLoading(true);
    try {
      const res = await authFetch(`/api/events/history?rule_id=${ruleId}&limit=20`);
      if (res.ok) setRuleEvents(await res.json());
    } catch {
      /* silent */
    } finally {
      setEventsLoading(false);
    }
  }, []);

  const fetchTelegramChannels = useCallback(async () => {
    setTelegramChannelsLoading(true);
    try {
      const res = await authFetch("/api/telegram/channels");
      if (res.ok) {
        const list: TelegramChannelOption[] = await res.json();
        setTelegramChannels(list);
      }
    } catch {
      /* silent */
    } finally {
      setTelegramChannelsLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    fetchRules();
    fetchCameras();
    fetchPersons();
    fetchTelegramChannels();
  }, [fetchRules, fetchCameras, fetchPersons, fetchTelegramChannels]);

  // Fetch events when a rule is selected, auto-refresh every 30s
  useEffect(() => {
    if (!selectedRule) {
      setRuleEvents([]);
      return;
    }
    fetchRuleEvents(selectedRule.id);
    const interval = setInterval(() => fetchRuleEvents(selectedRule.id), 30000);
    return () => clearInterval(interval);
  }, [selectedRule, fetchRuleEvents]);

  const resetForm = () => {
    setFormName("");
    setFormEnabled(true);
    setFormTriggerType("object_detected");
    setFormTriggerLabel("");
    setFormTriggerPersonId("");
    setFormTriggerSensitivity("medium");
    setFormTriggerAudioLabel("baby_cry");
    setFormTriggerAudioMinScore("0.35");
    setFormTriggerLineDirection("any");
    setFormTriggerGeomCamId("");
    setFormTriggerGeomPoints([]);
    setFormTriggerLoiterSeconds("30");
    setFormTriggerObjectClass("");
    setFormCondCameras([]);
    setFormScheduleMode("always");
    setFormCondDays([]);
    setFormCondTimeAfter("");
    setFormCondTimeBefore("");
    setFormCondConfidence("any");
    setFormActionType("notify");
    setFormActionUrl("");
    setFormActionMethod("POST");
    setFormActionMessage("");
    setFormActionSeverity("info");
    setFormActionAuthType("none");
    setFormActionAuthToken("");
    setFormActionAuthHeader("X-API-Key");
    setFormActionAuthKey("");
    setFormActionAuthUser("");
    setFormActionAuthPass("");
    setFormActionPayloadTemplate("");
    setFormActionUseCustomPayload(false);
    setFormPayloadError("");
    setFormActionEmailTo("");
    setFormActionEmailSubject("");
    setFormActionEmailBody("");
    setFormActionTelegramChannelId("");
    setFormActionTelegramTemplate(
      "<b>{rule_name}</b> on {camera_name}\n{vlm_description}\n{detections_summary}",
    );
    setFormActionTelegramSilent(false);
    setFormActionTelegramThumbnail(false);
    setFormCooldown("300");
    setFormError("");
  };

  const openCreate = () => {
    setEditRule(null);
    resetForm();
    setShowModal(true);
  };

  const openEdit = (r: Rule) => {
    setEditRule(r);
    setFormName(r.name);
    setFormEnabled(r.enabled);

    const tp = r.trigger_pattern;
    setFormTriggerType((tp.type as string) || "any");
    setFormTriggerLabel((tp.label as string) || "");
    setFormTriggerPersonId((tp.person_id as string) || "");
    setFormTriggerAudioLabel((tp.label as string) || "baby_cry");
    setFormTriggerAudioMinScore(tp.min_score != null ? String(tp.min_score) : "0.35");
    setFormTriggerLineDirection((tp.direction as string) || "any");
    setFormTriggerGeomCamId((tp.camera_id as string) || "");
    const pts = tp.points as number[][] | undefined;
    setFormTriggerGeomPoints(Array.isArray(pts) ? pts : []);
    setFormTriggerLoiterSeconds(tp.threshold_seconds != null ? String(tp.threshold_seconds) : "30");
    setFormTriggerObjectClass((tp.label as string) || "");
    setFormTriggerClapCount(tp.count != null ? String(tp.count) : "2");
    setFormTriggerPhrases(Array.isArray(tp.phrases) ? (tp.phrases as string[]) : []);
    setFormTriggerPhraseMatch((tp.match as "any" | "all") === "all" ? "all" : "any");
    // Map min_score back to sensitivity level
    const ms = tp.min_score as number | undefined;
    if (ms != null) {
      if (ms <= 0.02) setFormTriggerSensitivity("very_high");
      else if (ms <= 0.05) setFormTriggerSensitivity("high");
      else if (ms <= 0.15) setFormTriggerSensitivity("medium");
      else setFormTriggerSensitivity("low");
    } else {
      setFormTriggerSensitivity("medium");
    }

    const cond = r.conditions || {};
    const camIds = cond.camera_ids as string[] | undefined;
    const camId = cond.camera_id as string | undefined;
    setFormCondCameras(camIds || (camId ? [camId] : []));
    const days = cond.days as string[] | undefined;
    setFormCondDays(days || []);
    const hasSchedule = !!(cond.time_after || cond.time_before || (days && days.length > 0));
    setFormScheduleMode(hasSchedule ? "custom" : "always");
    setFormCondTimeAfter((cond.time_after as string) || "");
    setFormCondTimeBefore((cond.time_before as string) || "");
    // Map min_confidence back to label
    const mc = cond.min_confidence as number | undefined;
    if (mc != null) {
      if (mc >= 0.8) setFormCondConfidence("very_high");
      else if (mc >= 0.6) setFormCondConfidence("high");
      else if (mc >= 0.3) setFormCondConfidence("medium");
      else setFormCondConfidence("low");
    } else {
      setFormCondConfidence("any");
    }

    const acts = Array.isArray(r.actions) ? r.actions[0] : r.actions;
    setFormActionType((acts?.type as string) || "notify");
    setFormActionUrl((acts?.url as string) || "");
    setFormActionMethod((acts?.method as string) || "POST");
    setFormActionMessage((acts?.message as string) || "");
    setFormActionSeverity((acts?.severity as string) || "info");

    // Restore auth config
    const auth = acts?.auth as Record<string, string> | undefined;
    if (auth) {
      setFormActionAuthType(auth.type || "none");
      setFormActionAuthToken(auth.token || "");
      setFormActionAuthHeader(auth.header || "X-API-Key");
      setFormActionAuthKey(auth.key || "");
      setFormActionAuthUser(auth.username || "");
      setFormActionAuthPass(auth.password || "");
    } else {
      setFormActionAuthType("none");
      setFormActionAuthToken("");
      setFormActionAuthHeader("X-API-Key");
      setFormActionAuthKey("");
      setFormActionAuthUser("");
      setFormActionAuthPass("");
    }

    // Restore email fields
    setFormActionEmailTo((acts?.to as string) || "");
    setFormActionEmailSubject((acts?.subject as string) || "");
    setFormActionEmailBody((acts?.body as string) || "");

    // Restore telegram fields
    setFormActionTelegramChannelId((acts?.channel_id as string) || "");
    setFormActionTelegramTemplate(
      (acts?.template as string) ||
        "<b>{rule_name}</b> on {camera_name}\n{vlm_description}\n{detections_summary}",
    );
    setFormActionTelegramSilent(Boolean(acts?.silent));
    setFormActionTelegramThumbnail(Boolean(acts?.include_thumbnail));

    // Restore payload template
    const pt = acts?.payload_template;
    if (pt) {
      setFormActionUseCustomPayload(true);
      setFormActionPayloadTemplate(JSON.stringify(pt, null, 2));
    } else {
      setFormActionUseCustomPayload(false);
      setFormActionPayloadTemplate("");
    }
    setFormPayloadError("");

    setFormCooldown(String(r.cooldown_seconds));
    setFormError("");
    setShowModal(true);
  };

  const formSummary = useMemo(() => {
    const triggerPattern: Record<string, unknown> = { type: formTriggerType };
    if (formTriggerType === "object_detected" && formTriggerLabel) triggerPattern.label = formTriggerLabel;
    if (formTriggerType === "face_recognized" && formTriggerPersonId) triggerPattern.person_id = formTriggerPersonId;
    if (formTriggerType === "motion") triggerPattern.min_score = 0.08;
    if (formTriggerType === "audio_event") {
      triggerPattern.label = formTriggerAudioLabel;
      triggerPattern.min_score = parseFloat(formTriggerAudioMinScore) || 0.3;
    }
    if (formTriggerType === "loitering") {
      if (formTriggerGeomCamId) triggerPattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length >= 3) triggerPattern.points = formTriggerGeomPoints;
      triggerPattern.threshold_seconds = parseInt(formTriggerLoiterSeconds) || 30;
      if (formTriggerObjectClass) triggerPattern.label = formTriggerObjectClass;
    }
    if (formTriggerType === "line_cross") {
      if (formTriggerGeomCamId) triggerPattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length === 2) triggerPattern.points = formTriggerGeomPoints;
      if (formTriggerLineDirection !== "any") triggerPattern.direction = formTriggerLineDirection;
      if (formTriggerObjectClass) triggerPattern.label = formTriggerObjectClass;
    }

    const action: Record<string, unknown> = { type: formActionType };
    if (formActionType === "webhook" || formActionType === "api_call") {
      action.url = formActionUrl || "...";
      if (formActionType === "api_call") action.method = formActionMethod;
    }
    if (formActionType === "notify") {
      action.message = formActionMessage || "Rule triggered";
      action.severity = formActionSeverity;
    }

    const schedule = formScheduleMode === "custom"
      ? describeSchedule(formCondDays.length > 0 ? formCondDays : undefined, formCondTimeAfter || undefined, formCondTimeBefore || undefined)
      : "";

    return composeSummary(
      describeTrigger(triggerPattern),
      resolveCameraNames(formCondCameras, cameras),
      schedule,
      describeActions(action),
      parseInt(formCooldown) || 0,
    );
  }, [formTriggerType, formTriggerLabel, formTriggerPersonId, formActionType, formActionUrl, formActionMethod, formActionMessage, formActionSeverity, formScheduleMode, formCondDays, formCondTimeAfter, formCondTimeBefore, formCondCameras, cameras, formCooldown]);

  const buildPayload = () => {
    const trigger_pattern: Record<string, unknown> = { type: formTriggerType };
    if (formTriggerType === "object_detected" && formTriggerLabel) {
      trigger_pattern.label = formTriggerLabel;
    }
    if (formTriggerType === "face_recognized" && formTriggerPersonId) {
      trigger_pattern.person_id = formTriggerPersonId;
    }
    if (formTriggerType === "motion") {
      const sensitivityMap: Record<string, number> = {
        very_high: 0.01,
        high: 0.03,
        medium: 0.08,
        low: 0.2,
      };
      trigger_pattern.min_score = sensitivityMap[formTriggerSensitivity] ?? 0.08;
    }
    if (formTriggerType === "audio_event") {
      trigger_pattern.label = formTriggerAudioLabel;
      trigger_pattern.min_score = parseFloat(formTriggerAudioMinScore) || 0.3;
    }
    if (formTriggerType === "clap_pattern") {
      trigger_pattern.count = parseInt(formTriggerClapCount) || 2;
    }
    if (formTriggerType === "speech_phrase") {
      trigger_pattern.phrases = formTriggerPhrases;
      trigger_pattern.match = formTriggerPhraseMatch;
    }
    if (formTriggerType === "loitering") {
      if (formTriggerGeomCamId) trigger_pattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length >= 3) trigger_pattern.points = formTriggerGeomPoints;
      trigger_pattern.threshold_seconds = parseInt(formTriggerLoiterSeconds) || 30;
      if (formTriggerObjectClass) trigger_pattern.label = formTriggerObjectClass;
    }
    if (formTriggerType === "line_cross") {
      if (formTriggerGeomCamId) trigger_pattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length === 2) trigger_pattern.points = formTriggerGeomPoints;
      if (formTriggerLineDirection !== "any") trigger_pattern.direction = formTriggerLineDirection;
      if (formTriggerObjectClass) trigger_pattern.label = formTriggerObjectClass;
    }

    const conditions: Record<string, unknown> = {};
    if (formCondCameras.length > 0) conditions.camera_ids = formCondCameras;
    if (formScheduleMode === "custom") {
      if (formCondTimeAfter) conditions.time_after = formCondTimeAfter;
      if (formCondTimeBefore) conditions.time_before = formCondTimeBefore;
      if (formCondDays.length > 0) conditions.days = formCondDays;
    }
    if (formCondConfidence !== "any") {
      const confMap: Record<string, number> = {
        low: 0.2,
        medium: 0.4,
        high: 0.6,
        very_high: 0.8,
      };
      conditions.min_confidence = confMap[formCondConfidence] ?? 0.4;
    }

    const action: Record<string, unknown> = { type: formActionType };
    if (formActionType === "webhook" || formActionType === "api_call") {
      action.url = formActionUrl;
      if (formActionType === "api_call") {
        action.method = formActionMethod;
      }

      // Auth config
      if (formActionAuthType !== "none") {
        const auth: Record<string, string> = { type: formActionAuthType };
        if (formActionAuthType === "bearer") auth.token = formActionAuthToken;
        if (formActionAuthType === "api_key") {
          auth.header = formActionAuthHeader;
          auth.key = formActionAuthKey;
        }
        if (formActionAuthType === "basic") {
          auth.username = formActionAuthUser;
          auth.password = formActionAuthPass;
        }
        action.auth = auth;
      }

      // Custom payload template
      if (formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
        try {
          action.payload_template = JSON.parse(formActionPayloadTemplate);
        } catch {
          // Will be caught by validation
        }
      }
    }
    if (formActionType === "broadcast" && formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
      try {
        action.payload_template = JSON.parse(formActionPayloadTemplate);
      } catch {
        // Will be caught by validation
      }
    }
    if (formActionType === "notify") {
      action.message = formActionMessage || "Rule '{rule_name}' triggered";
      action.severity = formActionSeverity;
    }
    if (formActionType === "email") {
      action.to = formActionEmailTo;
      action.subject = formActionEmailSubject || "Nurby alert. {{rule_name}}";
      action.body = formActionEmailBody || "Rule {{rule_name}} fired at {{timestamp}}";
    }
    if (formActionType === "telegram") {
      action.channel_id = formActionTelegramChannelId;
      action.template = formActionTelegramTemplate;
      action.silent = formActionTelegramSilent;
      // Phase 1 sends text only. We persist the flag for Phase 2.
      action.include_thumbnail = formActionTelegramThumbnail;
    }
    if (formActionType === "vlm_call") {
      action.provider = formVlmProvider;
      action.model = formVlmModel;
      action.system = formVlmSystem;
      action.prompt = formVlmPrompt;
      action.attach_image = formVlmAttachImage;
      action.output = formVlmOutput || "result";
      action.max_retries = parseInt(formVlmMaxRetries) || 1;
      action.on_error = formVlmOnError;
      action.timeout_ms = parseInt(formVlmTimeoutMs) || 20000;
      if (formVlmUseSchema && formVlmSchemaText.trim()) {
        try {
          action.response_schema = JSON.parse(formVlmSchemaText);
        } catch {
          // caught in validation
        }
      }
    }

    return {
      name: formName.trim(),
      enabled: formEnabled,
      trigger_pattern,
      conditions: Object.keys(conditions).length > 0 ? conditions : null,
      actions: action,
      cooldown_seconds: parseInt(formCooldown) || 300,
    };
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError("Name is required");
      return;
    }
    if ((formActionType === "webhook" || formActionType === "api_call") && !formActionUrl.trim()) {
      setFormError("URL is required");
      return;
    }
    if (formActionType === "email" && !formActionEmailTo.trim()) {
      setFormError("Recipient email is required");
      return;
    }
    if (formActionType === "telegram") {
      if (!formActionTelegramChannelId) {
        setFormError("Pick a Telegram channel");
        return;
      }
      if (!formActionTelegramTemplate.trim()) {
        setFormError("Telegram message template cannot be empty");
        return;
      }
    }
    if (formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
      try {
        JSON.parse(formActionPayloadTemplate);
      } catch {
        setFormError("Payload template is not valid JSON");
        return;
      }
    }

    setSubmitting(true);
    setFormError("");
    const body = buildPayload();

    try {
      let res: Response;
      if (editRule) {
        res = await authFetch(`/api/rules/${editRule.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        res = await authFetch("/api/rules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }

      if (!res.ok) {
        setFormError("Failed to save rule");
        return;
      }

      setShowModal(false);
      fetchRules();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await authFetch(`/api/rules/${id}`, { method: "DELETE" });
      if (selectedRule?.id === id) setSelectedRule(null);
      fetchRules();
    } catch {
      /* silent */
    }
  };

  const handleToggle = async (rule: Rule) => {
    try {
      await authFetch(`/api/rules/${rule.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...rule, enabled: !rule.enabled }),
      });
      fetchRules();
    } catch {
      /* silent */
    }
  };

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {rules.length} rule{rules.length !== 1 ? "s" : ""} configured
          </p>
        </div>
        <button
          onClick={openCreate}
          className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
        >
          + Create rule
        </button>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground py-20 text-center">
          Loading.
        </div>
      ) : rules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
            ?
          </div>
          <p className="text-muted-foreground text-sm mb-4">
            No rules created yet. Rules let you define triggers, conditions,
            and actions to automate your monitoring.
          </p>
          <button
            onClick={openCreate}
            className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
          >
            + Create first rule
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-12 gap-6">
          {/* Rule list */}
          <section className="col-span-8 space-y-3">
            {rules.map((r) => (
              <div
                key={r.id}
                onClick={() => setSelectedRule(r)}
                className={`rounded-lg border p-4 cursor-pointer transition-colors ${
                  selectedRule?.id === r.id
                    ? "border-accent bg-card"
                    : "border-border bg-card hover:border-muted-foreground/30"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggle(r);
                      }}
                      className={`w-8 h-5 rounded-full relative transition-colors ${
                        r.enabled ? "bg-green-500" : "bg-muted"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                          r.enabled ? "left-3.5" : "left-0.5"
                        }`}
                      />
                    </button>
                    <div>
                      <div className="font-medium">{r.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {describeTrigger(r.trigger_pattern)}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(r);
                      }}
                      className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
                    >
                      Edit
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(r.id);
                      }}
                      className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors"
                    >
                      Del
                    </button>
                  </div>
                </div>
                <div className="mt-2 text-xs italic text-muted-foreground/80 leading-relaxed">
                  {buildRuleSummary(r, cameras)}
                </div>
              </div>
            ))}
          </section>

          {/* Preview panel */}
          <aside className="col-span-4">
            <div className="sticky top-20 rounded-lg border border-border bg-card p-5">
              <div className="flex items-center gap-2 mb-4">
                <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Preview
                </span>
              </div>
              {selectedRule ? (
                <div className="space-y-3 text-sm">
                  <SummaryCard text={buildRuleSummary(selectedRule, cameras)} />
                  <div>
                    <span className="text-muted-foreground text-xs">Name</span>
                    <div className="font-medium">{selectedRule.name}</div>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">Status</span>
                    <div className="flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          selectedRule.enabled ? "bg-green-500" : "bg-yellow-500"
                        }`}
                      />
                      {selectedRule.enabled ? "Active" : "Disabled"}
                    </div>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">Trigger</span>
                    <div>{describeTrigger(selectedRule.trigger_pattern)}</div>
                  </div>
                  {selectedRule.conditions && Object.keys(selectedRule.conditions).length > 0 && (
                    <div>
                      <span className="text-muted-foreground text-xs">Conditions</span>
                      <div className="text-xs mt-1 space-y-1">
                        {(() => {
                          const cond = selectedRule.conditions!;
                          const camIds = (cond.camera_ids as string[]) || (cond.camera_id ? [cond.camera_id as string] : []);
                          const parts: string[] = [];
                          if (camIds.length > 0) {
                            const names = camIds.map((cid) => {
                              const cam = cameras.find((c) => c.id === cid);
                              return cam ? cam.name : cid.slice(0, 8);
                            });
                            parts.push(`Cameras. ${names.join(", ")}`);
                          }
                          const days = cond.days as string[] | undefined;
                          if (days && days.length > 0 && days.length < 7) {
                            parts.push(`Days. ${days.map((d) => d.charAt(0).toUpperCase() + d.slice(1)).join(", ")}`);
                          }
                          if (cond.time_after || cond.time_before) {
                            parts.push(`Hours. ${cond.time_after || "00:00"} to ${cond.time_before || "23:59"}`);
                          }
                          if (cond.min_confidence) {
                            const mc = cond.min_confidence as number;
                            const label = mc >= 0.8 ? "Very high" : mc >= 0.6 ? "High" : mc >= 0.4 ? "Medium" : "Low";
                            parts.push(`Confidence. ${label} (${Math.round(mc * 100)}%+)`);
                          }
                          return parts.map((p, i) => <div key={i}>{p}</div>);
                        })()}
                      </div>
                    </div>
                  )}
                  <div>
                    <span className="text-muted-foreground text-xs">Actions</span>
                    <div>{describeActions(selectedRule.actions)}</div>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">Cooldown</span>
                    <div>{selectedRule.cooldown_seconds}s between fires</div>
                  </div>
                  <div>
                    <span className="text-muted-foreground text-xs">Created</span>
                    <div>{new Date(selectedRule.created_at).toLocaleString()}</div>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground leading-relaxed">
                  Select a rule to see its configuration preview.
                </p>
              )}
            </div>

            {/* Execution Log */}
            {selectedRule && (
              <div className="mt-4 rounded-lg border border-border bg-card p-5">
                <div className="flex items-center gap-2 mb-4">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Execution Log
                  </span>
                </div>
                {eventsLoading && ruleEvents.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Loading events.</p>
                ) : ruleEvents.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No events fired yet for this rule.</p>
                ) : (
                  <div className="space-y-2 max-h-[400px] overflow-y-auto">
                    {ruleEvents.map((ev) => (
                      <div
                        key={ev.id}
                        onClick={() => setExpandedEventId(expandedEventId === ev.id ? null : ev.id)}
                        className="rounded-md border border-border bg-background p-3 cursor-pointer hover:border-muted-foreground/30 transition-colors"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span
                              className={`w-2 h-2 rounded-full ${
                                ev.action_status === "success"
                                  ? "bg-green-500"
                                  : ev.action_status === "failed"
                                  ? "bg-red-500"
                                  : "bg-yellow-500"
                              }`}
                            />
                            {ev.action_type && (
                              <span className="px-1.5 py-0.5 text-[10px] rounded bg-muted text-muted-foreground font-mono">
                                {ev.action_type}
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] text-muted-foreground">
                            {new Date(ev.fired_at).toLocaleString()}
                          </span>
                        </div>
                        {ev.action_status === "failed" && ev.action_error && (
                          <div className="mt-1.5 text-[11px] text-red-400 truncate">
                            {ev.action_error}
                          </div>
                        )}
                        {expandedEventId === ev.id && (
                          <div className="mt-3 pt-3 border-t border-border">
                            <div className="text-[10px] text-muted-foreground mb-1">Payload</div>
                            <pre className="text-[10px] font-mono bg-muted/50 rounded p-2 overflow-x-auto max-h-40 overflow-y-auto whitespace-pre-wrap">
                              {ev.payload ? JSON.stringify(ev.payload, null, 2) : "No payload"}
                            </pre>
                            {ev.action_error && (
                              <div className="mt-2">
                                <div className="text-[10px] text-muted-foreground mb-1">Error</div>
                                <div className="text-[11px] text-red-400 break-words">{ev.action_error}</div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      )}

      {/* Create / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowModal(false)}
          />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-3xl shadow-xl max-h-[90vh] overflow-y-auto">
            <h2 className="text-lg font-semibold mb-4">
              {editRule ? "Edit rule" : "Create rule"}
            </h2>

            <div className="space-y-4">
              {/* Name */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Rule name
                </label>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                  placeholder="e.g. Person at front door"
                  autoFocus
                />
              </div>

              {/* Enabled */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formEnabled}
                  onChange={(e) => setFormEnabled(e.target.checked)}
                  className="accent-green-500"
                />
                <span className="text-sm">Enabled</span>
              </label>

              {/* Trigger */}
              <fieldset className="border border-border rounded-md p-3 space-y-3">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  When should this rule fire
                </legend>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {TRIGGER_TYPES.map((t) => {
                    const selected = formTriggerType === t.value;
                    const accent = TRIGGER_ACCENTS[t.accent] || TRIGGER_ACCENTS.slate;
                    return (
                      <button
                        key={t.value}
                        type="button"
                        onClick={() => setFormTriggerType(t.value)}
                        className={`relative text-left rounded-md border p-3 transition-all ${
                          selected
                            ? `${accent.active} ring-2`
                            : "border-border bg-background hover:bg-muted/60"
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <t.icon className={selected ? "text-foreground" : "text-muted-foreground"} />
                          <span className="text-sm font-medium">{t.label}</span>
                          {selected && <span className={`ml-auto w-2 h-2 rounded-full ${accent.dot}`} />}
                        </div>
                        <div className="text-[11px] text-muted-foreground leading-snug">{t.desc}</div>
                      </button>
                    );
                  })}
                </div>

                {formTriggerType === "object_detected" && (
                  <ModelClassPicker
                    value={formTriggerLabel}
                    onChange={setFormTriggerLabel}
                    activeModels={activeModels}
                    classes={modelClasses}
                    loading={modelClassesLoading}
                    anyLabel="Any object"
                  />
                )}

                {formTriggerType === "face_recognized" && (
                  <div className="space-y-2">
                    <label className="text-xs text-muted-foreground block">Person</label>
                    {persons.length === 0 ? (
                      <p className="text-xs text-muted-foreground px-2 py-3 rounded-md border border-dashed border-border">
                        No people yet. Add someone on the People page first.
                      </p>
                    ) : (
                      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-60 overflow-y-auto">
                        <button
                          type="button"
                          onClick={() => setFormTriggerPersonId("")}
                          className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                            formTriggerPersonId === ""
                              ? "border-sky-500 bg-sky-500/10 ring-2 ring-sky-500/40"
                              : "border-border bg-background hover:bg-muted/60"
                          }`}
                        >
                          <span className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs text-muted-foreground flex-shrink-0">*</span>
                          <div className="min-w-0">
                            <div className="text-sm font-medium truncate">Anyone known</div>
                            <div className="text-[10px] text-muted-foreground truncate">Any recognized face</div>
                          </div>
                        </button>
                        {persons.map((p) => {
                          const selected = formTriggerPersonId === p.id;
                          const initial = (p.display_name || "?").slice(0, 1).toUpperCase();
                          return (
                            <button
                              key={p.id}
                              type="button"
                              onClick={() => setFormTriggerPersonId(p.id)}
                              className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                                selected
                                  ? "border-sky-500 bg-sky-500/10 ring-2 ring-sky-500/40"
                                  : "border-border bg-background hover:bg-muted/60"
                              }`}
                            >
                              {p.photo_path ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img src={`/api/files/${p.photo_path}`} alt="" className="w-8 h-8 rounded-full object-cover flex-shrink-0" />
                              ) : (
                                <span className="w-8 h-8 rounded-full bg-sky-500/20 text-sky-300 flex items-center justify-center text-xs font-medium flex-shrink-0">{initial}</span>
                              )}
                              <div className="min-w-0">
                                <div className="text-sm font-medium truncate">{p.display_name}</div>
                                {p.relationship && <div className="text-[10px] text-muted-foreground truncate">{p.relationship}</div>}
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}

                {formTriggerType === "motion" && (
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1.5">
                      Motion sensitivity
                    </label>
                    <div className="grid grid-cols-4 gap-1">
                      {[
                        { value: "very_high", label: "Any movement", desc: "Triggers on smallest change" },
                        { value: "high", label: "Sensitive", desc: "Small movements" },
                        { value: "medium", label: "Normal", desc: "Moderate activity" },
                        { value: "low", label: "Only major", desc: "Large movements only" },
                      ].map((s) => (
                        <button
                          key={s.value}
                          type="button"
                          onClick={() => setFormTriggerSensitivity(s.value)}
                          className={`px-2 py-2 text-xs rounded border transition-colors text-center ${
                            formTriggerSensitivity === s.value
                              ? "border-accent bg-accent/10 text-accent"
                              : "border-border hover:bg-muted"
                          }`}
                        >
                          <div className="font-medium">{s.label}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {formTriggerType === "audio_event" && (
                  <div className="space-y-2">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Sound type</label>
                      <StyledSelect
                        value={formTriggerAudioLabel}
                        options={AUDIO_LABELS.map((a) => ({ value: a.value, label: a.label }))}
                        onChange={setFormTriggerAudioLabel}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">
                        Confidence threshold (0.1 low, 0.7 strict)
                      </label>
                      <input
                        type="number" min="0.05" max="0.95" step="0.05"
                        value={formTriggerAudioMinScore}
                        onChange={(e) => setFormTriggerAudioMinScore(e.target.value)}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                      />
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Detection runs locally on each camera&apos;s audio track. Needs an RTSP stream that publishes audio.
                    </p>
                  </div>
                )}

                {formTriggerType === "clap_pattern" && (
                  <div className="space-y-2">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Number of claps</label>
                      <div className="flex gap-1.5">
                        {["2", "3", "4", "5"].map((n) => (
                          <button
                            key={n}
                            type="button"
                            onClick={() => setFormTriggerClapCount(n)}
                            className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                              formTriggerClapCount === n
                                ? "border-rose-500 bg-rose-500/10 text-rose-300"
                                : "border-border text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            {n} claps
                          </button>
                        ))}
                      </div>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Counts claps that land within ~2s of each other.
                      Two claps lights one action, three claps another.
                      Needs audio enabled on the camera.
                    </p>
                  </div>
                )}

                {formTriggerType === "speech_phrase" && (
                  <div className="space-y-2">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Phrases to listen for</label>
                      <RulePhraseInput
                        values={formTriggerPhrases}
                        onChange={setFormTriggerPhrases}
                        placeholder='e.g. "lights on", "we have a problem"'
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Match mode</label>
                      <div className="flex gap-1.5">
                        {([
                          { v: "any", l: "Any phrase" },
                          { v: "all", l: "All phrases" },
                        ] as const).map((m) => (
                          <button
                            key={m.v}
                            type="button"
                            onClick={() => setFormTriggerPhraseMatch(m.v)}
                            className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                              formTriggerPhraseMatch === m.v
                                ? "border-rose-500 bg-rose-500/10 text-rose-300"
                                : "border-border text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            {m.l}
                          </button>
                        ))}
                      </div>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Matches transcript text from the camera&apos;s STT pipeline.
                      Case-insensitive substring. Needs audio + transcription enabled.
                    </p>
                  </div>
                )}

                {(formTriggerType === "loitering" || formTriggerType === "line_cross") && (
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1.5">Pick a camera</label>
                      {cameras.length === 0 ? (
                        <p className="text-xs text-muted-foreground px-2 py-3 rounded-md border border-dashed border-border">
                          No cameras yet. Add one on the Cameras page first.
                        </p>
                      ) : (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                          {cameras.map((cam) => {
                            const selected = formTriggerGeomCamId === cam.id;
                            return (
                              <button
                                key={cam.id}
                                type="button"
                                onClick={() => {
                                  setFormTriggerGeomCamId(cam.id);
                                  setFormTriggerGeomPoints([]);
                                }}
                                className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                                  selected
                                    ? "border-indigo-500 bg-indigo-500/10 ring-2 ring-indigo-500/40"
                                    : "border-border bg-background hover:bg-muted/60"
                                }`}
                              >
                                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                                  cam.status === "recording" ? "bg-green-500" :
                                  cam.status === "online" ? "bg-accent" :
                                  "bg-muted-foreground/40"
                                }`} />
                                <span className="text-sm font-medium truncate">{cam.name}</span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    {formTriggerGeomCamId && (() => {
                      const cam = cameras.find((c) => c.id === formTriggerGeomCamId);
                      if (!cam) return null;
                      return (
                        <div>
                          <label className="text-xs text-muted-foreground block mb-1.5">
                            {formTriggerType === "line_cross"
                              ? "Draw tripwire. Click two points on the feed."
                              : "Draw loiter zone. Click at least three points."}
                          </label>
                          <GeometryEditor
                            camera={cam}
                            mode={formTriggerType === "line_cross" ? "line" : "polygon"}
                            points={formTriggerGeomPoints}
                            onChange={setFormTriggerGeomPoints}
                          />
                        </div>
                      );
                    })()}

                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Which objects count (optional)</label>
                      <ModelClassPicker
                        value={formTriggerObjectClass}
                        onChange={setFormTriggerObjectClass}
                        activeModels={activeModels}
                        classes={modelClasses}
                        loading={modelClassesLoading}
                        anyLabel="Any tracked object"
                      />
                    </div>

                    {formTriggerType === "loitering" && (
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">
                          Loiter threshold (seconds inside the zone)
                        </label>
                        <div className="flex gap-1 flex-wrap">
                          {["10", "30", "60", "120", "300"].map((s) => (
                            <button
                              key={s}
                              type="button"
                              onClick={() => setFormTriggerLoiterSeconds(s)}
                              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                                formTriggerLoiterSeconds === s
                                  ? "border-accent bg-accent/10 text-accent"
                                  : "border-border hover:bg-muted"
                              }`}
                            >{parseInt(s) >= 60 ? `${Math.round(parseInt(s) / 60)} min` : `${s}s`}</button>
                          ))}
                          <input
                            type="number"
                            min="1"
                            value={formTriggerLoiterSeconds}
                            onChange={(e) => setFormTriggerLoiterSeconds(e.target.value)}
                            className="w-20 px-2 py-1.5 text-xs rounded border border-border bg-background"
                          />
                        </div>
                      </div>
                    )}

                    {formTriggerType === "line_cross" && (
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Direction</label>
                        <div className="grid grid-cols-3 gap-1">
                          {[
                            { v: "any", l: "Either way" },
                            { v: "in", l: "Inbound" },
                            { v: "out", l: "Outbound" },
                          ].map((d) => (
                            <button
                              key={d.v}
                              type="button"
                              onClick={() => setFormTriggerLineDirection(d.v)}
                              className={`px-2 py-2 text-xs rounded border transition-colors ${
                                formTriggerLineDirection === d.v
                                  ? "border-accent bg-accent/10 text-accent"
                                  : "border-border hover:bg-muted"
                              }`}
                            >{d.l}</button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </fieldset>

              {/* Conditions */}
              <fieldset className="border border-border rounded-md p-3 space-y-2">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  Conditions (optional)
                </legend>
                <div>
                  <label className="text-xs text-muted-foreground block mb-1">Cameras</label>
                  {cameras.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No cameras added yet</p>
                  ) : (
                    <div className="space-y-1.5 max-h-36 overflow-y-auto rounded-md border border-border bg-background p-2">
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="checkbox"
                          checked={formCondCameras.length === 0}
                          onChange={() => setFormCondCameras([])}
                          className="accent-green-500"
                        />
                        <span className="text-muted-foreground">All cameras</span>
                      </label>
                      {cameras.map((cam) => (
                        <label key={cam.id} className="flex items-center gap-2 cursor-pointer text-sm">
                          <input
                            type="checkbox"
                            checked={formCondCameras.includes(cam.id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setFormCondCameras([...formCondCameras, cam.id]);
                              } else {
                                setFormCondCameras(formCondCameras.filter((c) => c !== cam.id));
                              }
                            }}
                            className="accent-green-500"
                          />
                          <span>{cam.name}</span>
                          <span className={`w-1.5 h-1.5 rounded-full ${
                            cam.status === "recording" ? "bg-green-500" : cam.status === "online" ? "bg-accent" : "bg-muted-foreground/40"
                          }`} />
                        </label>
                      ))}
                    </div>
                  )}
                </div>
                {/* Schedule */}
                <div>
                  <label className="text-xs text-muted-foreground block mb-1.5">Schedule</label>
                  <div className="flex gap-1 mb-2">
                    <button
                      type="button"
                      onClick={() => setFormScheduleMode("always")}
                      className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                        formScheduleMode === "always"
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      Always on
                    </button>
                    <button
                      type="button"
                      onClick={() => setFormScheduleMode("custom")}
                      className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                        formScheduleMode === "custom"
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      Custom schedule
                    </button>
                  </div>

                  {formScheduleMode === "custom" && (
                    <div className="space-y-2 pl-1">
                      {/* Days of week */}
                      <div>
                        <label className="text-[10px] text-muted-foreground block mb-1">Active on</label>
                        <div className="flex gap-1">
                          {[
                            { value: "mon", label: "M" },
                            { value: "tue", label: "T" },
                            { value: "wed", label: "W" },
                            { value: "thu", label: "T" },
                            { value: "fri", label: "F" },
                            { value: "sat", label: "S" },
                            { value: "sun", label: "S" },
                          ].map((day) => (
                            <button
                              key={day.value}
                              type="button"
                              onClick={() => {
                                setFormCondDays((prev) =>
                                  prev.includes(day.value)
                                    ? prev.filter((d) => d !== day.value)
                                    : [...prev, day.value]
                                );
                              }}
                              className={`w-8 h-8 text-xs rounded-full border transition-colors ${
                                formCondDays.includes(day.value)
                                  ? "border-accent bg-accent/20 text-accent"
                                  : "border-border hover:bg-muted text-muted-foreground"
                              }`}
                            >
                              {day.label}
                            </button>
                          ))}
                          <button
                            type="button"
                            onClick={() => {
                              if (formCondDays.length === 7) {
                                setFormCondDays([]);
                              } else {
                                setFormCondDays(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
                              }
                            }}
                            className="px-2 h-8 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground ml-1"
                          >
                            {formCondDays.length === 7 ? "None" : "All"}
                          </button>
                        </div>
                        {formCondDays.length === 0 && (
                          <span className="text-[10px] text-muted-foreground">No days selected = every day</span>
                        )}
                      </div>

                      {/* Time range */}
                      <div>
                        <label className="text-[10px] text-muted-foreground block mb-1">Active between</label>
                        <div className="flex items-center gap-2">
                          <input
                            type="time"
                            value={formCondTimeAfter}
                            onChange={(e) => setFormCondTimeAfter(e.target.value)}
                            className="flex-1 px-2 py-1.5 rounded-md bg-background border border-border text-sm"
                          />
                          <span className="text-xs text-muted-foreground">to</span>
                          <input
                            type="time"
                            value={formCondTimeBefore}
                            onChange={(e) => setFormCondTimeBefore(e.target.value)}
                            className="flex-1 px-2 py-1.5 rounded-md bg-background border border-border text-sm"
                          />
                        </div>
                        {!formCondTimeAfter && !formCondTimeBefore && (
                          <span className="text-[10px] text-muted-foreground">No times set = all day</span>
                        )}
                      </div>

                      {/* Quick presets */}
                      <div className="flex gap-1">
                        {[
                          { label: "Daytime", after: "07:00", before: "19:00", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] },
                          { label: "Nighttime", after: "19:00", before: "07:00", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] },
                          { label: "Weekdays", after: "", before: "", days: ["mon", "tue", "wed", "thu", "fri"] },
                          { label: "Weekends", after: "", before: "", days: ["sat", "sun"] },
                        ].map((preset) => (
                          <button
                            key={preset.label}
                            type="button"
                            onClick={() => {
                              setFormCondTimeAfter(preset.after);
                              setFormCondTimeBefore(preset.before);
                              setFormCondDays(preset.days);
                            }}
                            className="px-2 py-1 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground transition-colors"
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Detection confidence */}
                <div>
                  <label className="text-xs text-muted-foreground block mb-1.5">
                    Detection confidence
                  </label>
                  <div className="grid grid-cols-5 gap-1">
                    {[
                      { value: "any", label: "Any", desc: "All detections" },
                      { value: "low", label: "Low+", desc: "20%+" },
                      { value: "medium", label: "Medium+", desc: "40%+" },
                      { value: "high", label: "High+", desc: "60%+" },
                      { value: "very_high", label: "Very high", desc: "80%+" },
                    ].map((c) => (
                      <button
                        key={c.value}
                        type="button"
                        onClick={() => setFormCondConfidence(c.value)}
                        className={`px-1 py-1.5 text-[11px] rounded border transition-colors text-center ${
                          formCondConfidence === c.value
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border hover:bg-muted"
                        }`}
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                  <span className="text-[10px] text-muted-foreground">
                    Higher confidence = fewer false positives but may miss some detections
                  </span>
                </div>
              </fieldset>

              {/* Action */}
              <fieldset className="border border-border rounded-md p-3 space-y-3">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  Action
                </legend>
                <StyledSelect
                  value={formActionType}
                  options={ACTION_TYPES.map((a) => ({ value: a.value, label: a.label }))}
                  onChange={setFormActionType}
                />

                {/* Webhook / API Call fields */}
                {(formActionType === "webhook" || formActionType === "api_call") && (
                  <div className="space-y-3">
                    {/* Method selector for API call */}
                    {formActionType === "api_call" && (
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">HTTP Method</label>
                        <div className="flex gap-1">
                          {HTTP_METHODS.map((m) => (
                            <button
                              key={m}
                              type="button"
                              onClick={() => setFormActionMethod(m)}
                              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                                formActionMethod === m
                                  ? "border-accent bg-accent/10 text-accent"
                                  : "border-border hover:bg-muted"
                              }`}
                            >
                              {m}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* URL */}
                    <input
                      type="url"
                      value={formActionUrl}
                      onChange={(e) => setFormActionUrl(e.target.value)}
                      className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                      placeholder="https://api.example.com/endpoint"
                    />

                    {/* Authentication */}
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1.5">Authentication</label>
                      <div className="flex gap-1 mb-2">
                        {AUTH_TYPES.map((at) => (
                          <button
                            key={at.value}
                            type="button"
                            onClick={() => setFormActionAuthType(at.value)}
                            className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                              formActionAuthType === at.value
                                ? "border-accent bg-accent/10 text-accent"
                                : "border-border hover:bg-muted"
                            }`}
                          >
                            {at.label}
                          </button>
                        ))}
                      </div>

                      {formActionAuthType === "bearer" && (
                        <input
                          type="password"
                          value={formActionAuthToken}
                          onChange={(e) => setFormActionAuthToken(e.target.value)}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                          placeholder="Bearer token"
                        />
                      )}

                      {formActionAuthType === "api_key" && (
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={formActionAuthHeader}
                            onChange={(e) => setFormActionAuthHeader(e.target.value)}
                            className="w-1/3 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Header name"
                          />
                          <input
                            type="password"
                            value={formActionAuthKey}
                            onChange={(e) => setFormActionAuthKey(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="API key value"
                          />
                        </div>
                      )}

                      {formActionAuthType === "basic" && (
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={formActionAuthUser}
                            onChange={(e) => setFormActionAuthUser(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Username"
                          />
                          <input
                            type="password"
                            value={formActionAuthPass}
                            onChange={(e) => setFormActionAuthPass(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Password"
                          />
                        </div>
                      )}
                    </div>

                    {/* Custom payload toggle + editor */}
                    <div>
                      <label className="flex items-center gap-2 cursor-pointer mb-2">
                        <input
                          type="checkbox"
                          checked={formActionUseCustomPayload}
                          onChange={(e) => {
                            setFormActionUseCustomPayload(e.target.checked);
                            if (e.target.checked && !formActionPayloadTemplate) {
                              setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                            }
                            setFormPayloadError("");
                          }}
                          className="accent-green-500"
                        />
                        <span className="text-xs">Custom payload template</span>
                      </label>

                      {formActionUseCustomPayload && (
                        <div className="space-y-2">
                          <textarea
                            value={formActionPayloadTemplate}
                            onChange={(e) => {
                              setFormActionPayloadTemplate(e.target.value);
                              setFormPayloadError("");
                              try {
                                if (e.target.value.trim()) JSON.parse(e.target.value);
                              } catch {
                                setFormPayloadError("Invalid JSON");
                              }
                            }}
                            rows={8}
                            className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                            placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                            spellCheck={false}
                          />
                          {formPayloadError && (
                            <div className="text-[10px] text-red-400">{formPayloadError}</div>
                          )}
                          <div>
                            <div className="text-[10px] text-muted-foreground mb-1">
                              Available variables (click to insert)
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {TEMPLATE_VARIABLES.map((v) => (
                                <button
                                  key={v.key}
                                  type="button"
                                  title={v.desc}
                                  onClick={() => {
                                    setFormActionPayloadTemplate(
                                      (prev) => prev + `"{{${v.key}}}"`
                                    );
                                  }}
                                  className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                                >
                                  {`{{${v.key}}}`}
                                </button>
                              ))}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Broadcast custom payload */}
                {formActionType === "broadcast" && (
                  <div>
                    <label className="flex items-center gap-2 cursor-pointer mb-2">
                      <input
                        type="checkbox"
                        checked={formActionUseCustomPayload}
                        onChange={(e) => {
                          setFormActionUseCustomPayload(e.target.checked);
                          if (e.target.checked && !formActionPayloadTemplate) {
                            setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                          }
                          setFormPayloadError("");
                        }}
                        className="accent-green-500"
                      />
                      <span className="text-xs">Custom broadcast payload</span>
                    </label>

                    {formActionUseCustomPayload && (
                      <div className="space-y-2">
                        <textarea
                          value={formActionPayloadTemplate}
                          onChange={(e) => {
                            setFormActionPayloadTemplate(e.target.value);
                            setFormPayloadError("");
                            try {
                              if (e.target.value.trim()) JSON.parse(e.target.value);
                            } catch {
                              setFormPayloadError("Invalid JSON");
                            }
                          }}
                          rows={6}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                          placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                          spellCheck={false}
                        />
                        {formPayloadError && (
                          <div className="text-[10px] text-red-400">{formPayloadError}</div>
                        )}
                        <div className="flex flex-wrap gap-1">
                          {TEMPLATE_VARIABLES.map((v) => (
                            <button
                              key={v.key}
                              type="button"
                              title={v.desc}
                              onClick={() => {
                                setFormActionPayloadTemplate(
                                  (prev) => prev + `"{{${v.key}}}"`
                                );
                              }}
                              className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                            >
                              {`{{${v.key}}}`}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Notify fields */}
                {formActionType === "notify" && (
                  <>
                    <input
                      type="text"
                      value={formActionMessage}
                      onChange={(e) => setFormActionMessage(e.target.value)}
                      className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                      placeholder="Rule '{rule_name}' triggered"
                    />
                    <StyledSelect
                      value={formActionSeverity}
                      options={[
                        { value: "info", label: "Info" },
                        { value: "warning", label: "Warning" },
                        { value: "critical", label: "Critical" },
                      ]}
                      onChange={setFormActionSeverity}
                    />
                  </>
                )}

                {/* Email fields */}
                {formActionType === "email" && (
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Recipient</label>
                      <input
                        type="email"
                        value={formActionEmailTo}
                        onChange={(e) => setFormActionEmailTo(e.target.value)}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        placeholder="user@example.com"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Subject template</label>
                      <input
                        type="text"
                        value={formActionEmailSubject}
                        onChange={(e) => setFormActionEmailSubject(e.target.value)}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        placeholder="Nurby alert. {{rule_name}}"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Body template</label>
                      <textarea
                        value={formActionEmailBody}
                        onChange={(e) => setFormActionEmailBody(e.target.value)}
                        rows={4}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
                        placeholder="Rule {{rule_name}} fired at {{timestamp}} on camera {{camera_id}}"
                      />
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground mb-1">
                        Available variables (click to insert into body)
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {TEMPLATE_VARIABLES.map((v) => (
                          <button
                            key={v.key}
                            type="button"
                            title={v.desc}
                            onClick={() => {
                              setFormActionEmailBody(
                                (prev) => prev + `{{${v.key}}}`
                              );
                            }}
                            className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                          >
                            {`{{${v.key}}}`}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                      SMTP must be configured in Settings for email delivery to work.
                    </div>
                  </div>
                )}

                {/* Telegram fields */}
                {formActionType === "telegram" && (
                  <div className="space-y-3">
                    {telegramChannelsLoading ? (
                      <div className="text-xs text-muted-foreground">Loading Telegram channels.</div>
                    ) : telegramChannels.filter((c) => c.enabled && c.pairing_status === "paired").length === 0 ? (
                      <div className="text-xs text-muted-foreground bg-muted/40 border border-border rounded px-3 py-2">
                        No Telegram channels yet. Add one in{" "}
                        <a href="/settings" className="underline text-accent">
                          Settings → Notifications →
                        </a>
                      </div>
                    ) : (
                      <>
                        <div>
                          <label className="text-xs text-muted-foreground block mb-1">
                            Telegram channel
                          </label>
                          <StyledSelect
                            value={formActionTelegramChannelId}
                            onChange={setFormActionTelegramChannelId}
                            options={[
                              { value: "", label: "Pick a channel..." },
                              ...telegramChannels
                                .filter((c) => c.enabled && c.pairing_status === "paired")
                                .sort((a, b) => a.label.localeCompare(b.label))
                                .map((c) => ({
                                  value: c.id,
                                  label: `${c.label} · ${c.chat_title || "@" + (c.bot_username || "")}`,
                                })),
                            ]}
                          />
                        </div>

                        <div>
                          <label className="text-xs text-muted-foreground block mb-1">
                            Message template
                          </label>
                          <textarea
                            value={formActionTelegramTemplate}
                            onChange={(e) => setFormActionTelegramTemplate(e.target.value)}
                            rows={4}
                            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
                            placeholder="<b>{rule_name}</b> on {camera_name}"
                          />
                          <div className="text-[10px] text-muted-foreground mt-1">
                            HTML formatting is supported (e.g. &lt;b&gt;bold&lt;/b&gt;). Variables. click to insert.
                          </div>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {TELEGRAM_TEMPLATE_VARS.map((v) => (
                              <button
                                key={v.key}
                                type="button"
                                title={v.desc}
                                onClick={() =>
                                  setFormActionTelegramTemplate((prev) => prev + `{${v.key}}`)
                                }
                                className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
                              >
                                {`{${v.key}}`}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="flex flex-wrap gap-3">
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={formActionTelegramSilent}
                              onChange={(e) => setFormActionTelegramSilent(e.target.checked)}
                              className="accent-green-500"
                            />
                            <span className="text-xs">Silent (no sound, overrides channel default)</span>
                          </label>
                          <label
                            className="flex items-center gap-2 cursor-not-allowed opacity-60"
                            title="Coming soon. Phase 2."
                          >
                            <input
                              type="checkbox"
                              disabled
                              checked={formActionTelegramThumbnail}
                              onChange={(e) => setFormActionTelegramThumbnail(e.target.checked)}
                              className="accent-green-500"
                            />
                            <span className="text-xs">Include snapshot (Phase 2)</span>
                          </label>
                        </div>

                        <div className="text-[11px] text-muted-foreground bg-muted/40 rounded px-2 py-1.5">
                          {(() => {
                            const ch = telegramChannels.find(
                              (c) => c.id === formActionTelegramChannelId,
                            );
                            if (!ch) return "Send a Telegram message to the selected channel.";
                            const target = ch.chat_title || `@${ch.bot_username || "bot"}`;
                            return `Send a Telegram message to ${target}.`;
                          })()}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* VLM Call fields */}
                {formActionType === "vlm_call" && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Provider</label>
                        <StyledSelect
                          value={formVlmProvider}
                          options={VLM_PROVIDERS}
                          onChange={setFormVlmProvider}
                        />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Model</label>
                        <input
                          type="text"
                          value={formVlmModel}
                          onChange={(e) => setFormVlmModel(e.target.value)}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                          placeholder="gpt-4o-mini"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">System prompt</label>
                      <textarea
                        value={formVlmSystem}
                        onChange={(e) => setFormVlmSystem(e.target.value)}
                        rows={2}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono resize-y"
                        placeholder="{{defaults.system}}"
                      />
                      <label className="flex items-center gap-2 cursor-pointer mt-1">
                        <input
                          type="checkbox"
                          checked={formVlmSystem.startsWith("{{defaults.system}}")}
                          onChange={(e) => {
                            if (e.target.checked && !formVlmSystem.startsWith("{{defaults.system}}")) {
                              setFormVlmSystem(`{{defaults.system}}\n\n${formVlmSystem}`);
                            } else if (!e.target.checked) {
                              setFormVlmSystem(
                                formVlmSystem.replace(/^\{\{defaults\.system\}\}\n*/, ""),
                              );
                            }
                          }}
                          className="accent-green-500"
                        />
                        <span className="text-[11px] text-muted-foreground">Extend global default</span>
                      </label>
                    </div>

                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">User prompt</label>
                      <textarea
                        value={formVlmPrompt}
                        onChange={(e) => setFormVlmPrompt(e.target.value)}
                        rows={3}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
                      />
                      <div className="flex flex-wrap gap-1 mt-1">
                        {["description", "faces", "objects", "camera_name", "timestamp"].map((k) => (
                          <button
                            key={k}
                            type="button"
                            onClick={() => setFormVlmPrompt((p) => p + ` {{${k}}}`)}
                            className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
                          >{`{{${k}}}`}</button>
                        ))}
                      </div>
                    </div>

                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={formVlmAttachImage}
                        onChange={(e) => setFormVlmAttachImage(e.target.checked)}
                        className="accent-green-500"
                      />
                      <span className="text-xs">Attach snapshot image</span>
                    </label>

                    <div>
                      <label className="flex items-center gap-2 cursor-pointer mb-1">
                        <input
                          type="checkbox"
                          checked={formVlmUseSchema}
                          onChange={(e) => setFormVlmUseSchema(e.target.checked)}
                          className="accent-green-500"
                        />
                        <span className="text-xs">Structured JSON output</span>
                      </label>
                      {formVlmUseSchema && (
                        <div className="space-y-2">
                          <div className="flex flex-wrap gap-1">
                            {[
                              { key: "threat", label: "Threat level" },
                              { key: "notify", label: "Notify yes/no" },
                              { key: "intent", label: "Intent classifier" },
                              { key: "entities", label: "Entity counts" },
                            ].map((p) => (
                              <button
                                key={p.key}
                                type="button"
                                onClick={() => setFormVlmSchemaText(VLM_SCHEMA_PRESETS[p.key])}
                                className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground"
                              >
                                {p.label}
                              </button>
                            ))}
                          </div>
                          <textarea
                            value={formVlmSchemaText}
                            onChange={(e) => setFormVlmSchemaText(e.target.value)}
                            rows={8}
                            className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono resize-y"
                          />
                        </div>
                      )}
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Output variable</label>
                        <input
                          type="text"
                          value={formVlmOutput}
                          onChange={(e) => setFormVlmOutput(e.target.value.replace(/[^\w]/g, ""))}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
                          placeholder="result"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Max retries</label>
                        <input
                          type="number"
                          min={0}
                          max={3}
                          value={formVlmMaxRetries}
                          onChange={(e) => setFormVlmMaxRetries(e.target.value)}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">Timeout (ms)</label>
                        <input
                          type="number"
                          min={1000}
                          step={1000}
                          value={formVlmTimeoutMs}
                          onChange={(e) => setFormVlmTimeoutMs(e.target.value)}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">On error</label>
                      <StyledSelect
                        value={formVlmOnError}
                        options={[
                          { value: "continue", label: "Continue chain" },
                          { value: "stop", label: "Stop chain" },
                          { value: "fallback", label: "Use fallback value" },
                        ]}
                        onChange={setFormVlmOnError}
                      />
                    </div>
                    <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                      Reference the result in later actions with {"{{"}vars.{formVlmOutput || "result"}.field{"}}"}.
                    </div>
                  </div>
                )}
              </fieldset>

              {/* Cooldown */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Wait between alerts
                </label>
                <div className="grid grid-cols-5 gap-1">
                  {[
                    { value: "0", label: "None" },
                    { value: "30", label: "30 sec" },
                    { value: "300", label: "5 min" },
                    { value: "900", label: "15 min" },
                    { value: "3600", label: "1 hour" },
                  ].map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setFormCooldown(opt.value)}
                      className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                        formCooldown === opt.value
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <span className="text-[10px] text-muted-foreground">
                  Prevents repeated alerts for the same event
                </span>
              </div>

              <SummaryCard text={formSummary} className="p-3" />

              {formError && (
                <div className="text-xs text-red-400">{formError}</div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => setShowModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
              >
                {submitting ? "Saving." : editRule ? "Save" : "Create"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RulePhraseInput({
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
    const cleaned = draft.split(",").map((s) => s.trim()).filter(Boolean);
    if (!cleaned.length) return;
    onChange(Array.from(new Set([...values, ...cleaned])));
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 min-h-[2.25rem] px-2 py-1 rounded-md border border-border bg-background focus-within:border-accent">
      {values.map((v) => (
        <span
          key={v}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-rose-500/15 text-rose-300 border border-rose-500/30"
        >
          {v}
          <button
            type="button"
            onClick={() => onChange(values.filter((x) => x !== v))}
            className="text-rose-300/70 hover:text-rose-200"
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
        className="flex-1 min-w-[10rem] bg-transparent text-sm focus:outline-none"
      />
    </div>
  );
}
