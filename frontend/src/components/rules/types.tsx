// Shared types, constants, and helper utilities for the rules surface.
// Lifted verbatim from frontend/src/app/rules/page.tsx as part of the
// Wave 1 mechanical decomposition. No behavior changes.

import type React from "react";

export const WEBRTC_URL =
  process.env.NEXT_PUBLIC_WEBRTC_URL || "http://localhost:8889";

export function extractStreamName(streamUrl: string): string {
  try {
    const path = streamUrl.replace(/\/+$/, "");
    const lastSlash = path.lastIndexOf("/");
    return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
  } catch {
    return streamUrl;
  }
}

export interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  trigger_pattern: Record<string, unknown>;
  conditions: Record<string, unknown> | null;
  actions: Record<string, unknown> | Record<string, unknown>[];
  cooldown_seconds: number;
  created_at: string;
}

export interface EventEntry {
  id: string;
  rule_id: string | null;
  observation_id: string | null;
  fired_at: string;
  payload: Record<string, unknown> | null;
  acknowledged_at: string | null;
  action_status: string;
  action_error: string | null;
  action_type: string | null;
  acked_at?: string | null;
  acked_by_user_id?: string | null;
  acked_via?: string | null;
  muted_until?: string | null;
}

export interface Camera {
  id: string;
  name: string;
  status: string;
  stream_url?: string;
  width?: number;
  height?: number;
  detection_models?: { model: string; enabled?: boolean }[] | null;
}

export interface Person {
  id: string;
  display_name: string;
  relationship: string | null;
  photo_path: string | null;
}

export interface TriggerType {
  value: string;
  label: string;
  icon: (props: { className?: string }) => React.ReactElement;
  desc: string;
  accent: string;
  group: "vision" | "faces" | "motion" | "audio" | "spatial" | "any";
}

export interface SelectOption {
  value: string;
  label: string;
  hint?: string;
}

export type TelegramButtonAction = "ack" | "mute_event" | "snooze_rule" | "open";

export interface TelegramButton {
  label: string;
  action: TelegramButtonAction;
  duration_seconds?: number;
  url?: string;
}

export interface TelegramChannelOption {
  id: string;
  label: string;
  bot_username: string | null;
  chat_title: string | null;
  enabled: boolean;
  pairing_status: string;
  shared_with_household?: boolean;
  share_permissions?: "use" | "use_and_test";
  owned_by_me?: boolean;
  owner_display_name?: string | null;
}

// Minimal inline SVGs. 18px, stroke 1.75, currentColor.
export const Icon = {
  box: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" />
    </svg>
  ),
  user: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
    </svg>
  ),
  userCheck: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="m16 11 2 2 4-4" />
    </svg>
  ),
  userQ: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
      <path d="M17 11a2 2 0 1 1 3 1.7c-.4.3-1 .6-1 1.3" /><path d="M19 17h.01" />
    </svg>
  ),
  wave: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12c2 0 2-3 4-3s2 6 4 6 2-9 4-9 2 9 4 9 2-3 4-3" />
    </svg>
  ),
  speaker: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H2v6h4l5 4z" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  ),
  clock: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" />
    </svg>
  ),
  tripwire: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17 21 7" /><path d="m17 5 4 2-2 4" /><circle cx="6" cy="18" r="1.5" />
    </svg>
  ),
  spark: ({ className }: { className?: string }) => (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v18" /><path d="M3 12h18" /><path d="m5.6 5.6 12.8 12.8" /><path d="m18.4 5.6-12.8 12.8" />
    </svg>
  ),
};

export const TRIGGER_TYPES: TriggerType[] = [
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

export const TRIGGER_ACCENTS: Record<string, { active: string; dot: string }> = {
  green:  { active: "border-green-500 bg-green-500/10 ring-green-500/40",  dot: "bg-green-500" },
  blue:   { active: "border-sky-500 bg-sky-500/10 ring-sky-500/40",        dot: "bg-sky-500" },
  amber:  { active: "border-amber-500 bg-amber-500/10 ring-amber-500/40",  dot: "bg-amber-500" },
  rose:   { active: "border-rose-500 bg-rose-500/10 ring-rose-500/40",     dot: "bg-rose-500" },
  indigo: { active: "border-indigo-500 bg-indigo-500/10 ring-indigo-500/40", dot: "bg-indigo-500" },
  slate:  { active: "border-slate-400 bg-slate-400/10 ring-slate-400/40",  dot: "bg-slate-400" },
};

export const AUDIO_LABELS = [
  { value: "baby_cry", label: "Baby cry" },
  { value: "crying", label: "Crying / sobbing" },
  { value: "scream", label: "Scream / shout" },
  { value: "speech", label: "Speech" },
  { value: "glass_break", label: "Glass break" },
  { value: "alarm", label: "Alarm / siren" },
  { value: "bark", label: "Dog bark" },
  { value: "gunshot", label: "Gunshot / explosion" },
];

export const OBJECT_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle",
  "dog", "cat", "bird", "backpack", "handbag",
  "suitcase", "umbrella",
];

export const ACTION_TYPES = [
  { value: "webhook", label: "Webhook" },
  { value: "api_call", label: "API Call" },
  { value: "broadcast", label: "WebSocket broadcast" },
  { value: "notify", label: "Notification" },
  { value: "email", label: "Email" },
  { value: "telegram", label: "Telegram" },
  { value: "vlm_call", label: "VLM Call" },
  { value: "verify", label: "Verify with AI" },
];

export const TELEGRAM_TEMPLATE_VARS = [
  { key: "rule_name", desc: "Name of the rule that fired" },
  { key: "camera_name", desc: "Camera that produced the observation" },
  { key: "timestamp_local", desc: "Time of the observation in the camera's timezone" },
  { key: "vlm_description", desc: "Scene description from the VLM, if any" },
  { key: "detections_summary", desc: "Compact list of objects and faces detected" },
  { key: "observation_id", desc: "Database id of the observation" },
  { key: "event_id", desc: "Database id of the fired event" },
  { key: "event_url", desc: "Web UI deep link to the event (needs public base URL)" },
  { key: "recording_url", desc: "Direct link to the footage clip covering the alert" },
  { key: "thumbnail_url", desc: "Link to the observation thumbnail image" },
];

export const TELEGRAM_BUTTON_ACTION_OPTIONS: { value: TelegramButtonAction; label: string }[] = [
  { value: "ack", label: "Acknowledge" },
  { value: "mute_event", label: "Mute event" },
  { value: "snooze_rule", label: "Snooze rule" },
  { value: "open", label: "Open URL" },
];

export const TELEGRAM_DEFAULT_TEMPLATE =
  "🔔 {rule_name}\n📷 {camera_name} at {timestamp_local}\n\n{vlm_description}";

export const TELEGRAM_DEFAULT_BUTTONS: TelegramButton[] = [
  { label: "✓ Acknowledge", action: "ack" },
  { label: "🔕 Mute 10 min", action: "mute_event", duration_seconds: 600 },
  { label: "💤 Snooze rule 1h", action: "snooze_rule", duration_seconds: 3600 },
  { label: "📺 View clip", action: "open", url: "{event_url}" },
];

export const TELEGRAM_BUTTON_DURATION_DEFAULTS: Record<TelegramButtonAction, number | undefined> = {
  ack: undefined,
  mute_event: 600,
  snooze_rule: 3600,
  open: undefined,
};

export function isValidHttpUrlOrTemplate(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.includes("{") && trimmed.includes("}")) return true;
  return trimmed.startsWith("http://") || trimmed.startsWith("https://");
}

export const VLM_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "ollama", label: "Ollama" },
];

export const VLM_SCHEMA_PRESETS: Record<string, string> = {
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

export const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

export const AUTH_TYPES = [
  { value: "none", label: "No auth" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key header" },
  { value: "basic", label: "Basic auth" },
];

export const TEMPLATE_VARIABLES = [
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
  { key: "camera_name", desc: "Camera name" },
  { key: "recording_url", desc: "Direct link to the footage clip" },
  { key: "thumbnail_url", desc: "Observation thumbnail image link" },
  { key: "event_url", desc: "Web UI deep link to the event" },
];

export const DEFAULT_PAYLOAD_TEMPLATE = `{
  "event": "{{rule_name}}",
  "camera": "{{camera_name}}",
  "timestamp": "{{timestamp}}",
  "description": "{{vlm_description}}",
  "detections": "{{object_detections}}",
  "recording_url": "{{recording_url}}",
  "thumbnail_url": "{{thumbnail_url}}"
}`;

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"];
export const WEEKEND = ["sat", "sun"];

// Populated by the page so describeTrigger can resolve ids to names.
export const personLookup = new Map<string, string>();
export const cameraLookup = new Map<string, string>();

export function describeTrigger(pattern: Record<string, unknown>): string {
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

export function describeActions(actions: Record<string, unknown> | Record<string, unknown>[]): string {
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
      if (a.type === "verify") {
        const q = (a.question as string) || "...";
        return `confirm with AI that ${q}`;
      }
      return String(a.type);
    })
    .join(", ");
}

export function formatCooldown(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${seconds} seconds`;
  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "1 minute";
  return `${minutes} minutes`;
}

export function resolveCameraNames(camIds: string[], cameras: Camera[]): string {
  if (camIds.length === 0) return "any camera";
  const names = camIds.map((cid) => {
    const cam = cameras.find((c) => c.id === cid);
    return cam ? cam.name : cid.slice(0, 8);
  });
  return names.join(", ");
}

export function describeSchedule(
  days: string[] | undefined,
  timeAfter: string | undefined,
  timeBefore: string | undefined,
): string {
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

export function composeSummary(
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

// ── Rule test + replay shapes (mirror shared/schemas.py) ──

export interface RulePayload {
  name: string;
  enabled: boolean;
  trigger_pattern: Record<string, unknown>;
  conditions: Record<string, unknown> | null;
  actions: Record<string, unknown> | Record<string, unknown>[];
  cooldown_seconds: number;
}

export interface RuleTestActionPreview {
  index: number;
  action_type: string;
  rendered_action: Record<string, unknown>;
}

export interface RuleTestResponse {
  matched: boolean;
  reason: string;
  matched_trigger: boolean;
  matched_conditions: boolean;
  schedule_blocked: boolean;
  cooldown_active: boolean;
  synthesized_observation: Record<string, unknown>;
  would_fire: RuleTestActionPreview[];
}

export interface RuleReplaySample {
  observation_id: string;
  timestamp: string;
  camera_id: string | null;
  thumbnail_path: string | null;
  snippet: string | null;
}

export interface RuleReplayResponse {
  rule_id: string;
  hours: number;
  scanned: number;
  matched: number;
  first_matched_at: string | null;
  last_matched_at: string | null;
  samples: RuleReplaySample[];
}

// ── Multi-action chain. ActionDraft union ──
//
// Each card in the action chain editor holds a draft of one action.
// Drafts are converted to the dict shape the backend expects at submit
// time (see `draftToDict` in this file and `buildPayload` in RuleModal).
// The discriminated union here mirrors the action shapes accepted by
// `_validate_action_chain` in `shared/schemas.py`.

export type ActionType =
  | "webhook"
  | "api_call"
  | "broadcast"
  | "notify"
  | "email"
  | "telegram"
  | "vlm_call"
  | "verify";

export interface WebhookDraft {
  type: "webhook" | "api_call";
  url: string;
  method: string;
  authType: string;
  authToken: string;
  authHeader: string;
  authKey: string;
  authUser: string;
  authPass: string;
  // Optional HMAC signing secret. when set, Nurby signs the request
  // body with HMAC-SHA256 in the X-Nurby-Signature header.
  secret: string;
  useCustomPayload: boolean;
  payloadTemplate: string;
  payloadError: string;
}
export interface BroadcastDraft {
  type: "broadcast";
  useCustomPayload: boolean;
  payloadTemplate: string;
  payloadError: string;
}
export interface NotifyDraft {
  type: "notify";
  message: string;
  severity: string;
}
export interface EmailDraft {
  type: "email";
  to: string;
  subject: string;
  body: string;
}
export interface TelegramDraft {
  type: "telegram";
  channelId: string;
  template: string;
  silent: boolean;
  includeThumbnail: boolean;
  buttons: TelegramButton[];
  defaultsApplied: boolean;
}
export interface VlmCallDraft {
  type: "vlm_call";
  provider: string;
  model: string;
  system: string;
  prompt: string;
  attachImage: boolean;
  useSchema: boolean;
  schemaText: string;
  output: string;
  maxRetries: string;
  onError: string;
  timeoutMs: string;
}

export interface VerifyDraft {
  type: "verify";
  question: string;
  minConfidence: number;
  onFail: "stop" | "continue";
  providerId?: string;
}

export type ActionDraft =
  | WebhookDraft
  | BroadcastDraft
  | NotifyDraft
  | EmailDraft
  | TelegramDraft
  | VlmCallDraft
  | VerifyDraft;

export const MAX_ACTIONS_PER_RULE = 8;

// Build a fresh draft for a given type. Used when adding a new action
// card or switching a card's type. Drafts must be self-contained so a
// type switch fully wipes prior-type fields.
export function defaultDraftForType(type: ActionType): ActionDraft {
  switch (type) {
    case "webhook":
    case "api_call":
      return {
        type,
        url: "",
        method: "POST",
        authType: "none",
        authToken: "",
        authHeader: "X-API-Key",
        authKey: "",
        authUser: "",
        authPass: "",
        secret: "",
        useCustomPayload: false,
        payloadTemplate: "",
        payloadError: "",
      };
    case "broadcast":
      return { type, useCustomPayload: false, payloadTemplate: "", payloadError: "" };
    case "notify":
      return { type, message: "", severity: "info" };
    case "email":
      return { type, to: "", subject: "", body: "" };
    case "telegram":
      return {
        type,
        channelId: "",
        template: TELEGRAM_DEFAULT_TEMPLATE,
        silent: false,
        includeThumbnail: false,
        buttons: [...TELEGRAM_DEFAULT_BUTTONS],
        defaultsApplied: true,
      };
    case "vlm_call":
      return {
        type,
        provider: "openai",
        model: "gpt-4o-mini",
        system: "{{defaults.system}}",
        prompt: "Describe the scene. Focus on people, vehicles, and unusual activity.",
        attachImage: true,
        useSchema: false,
        schemaText: VLM_SCHEMA_PRESETS.threat,
        output: "result",
        maxRetries: "1",
        onError: "continue",
        timeoutMs: "20000",
      };
    case "verify":
      return {
        type,
        question: "",
        minConfidence: 0.6,
        onFail: "stop",
      };
  }
}

// Hydrate a draft from a backend action dict (one entry from
// rule.actions). Mirrors the field mapping that lived in the old
// RuleModal hydrate block.
export function dictToDraft(raw: Record<string, unknown>): ActionDraft {
  const t = (raw?.type as ActionType) || "notify";
  switch (t) {
    case "webhook":
    case "api_call": {
      const auth = (raw.auth as Record<string, string> | undefined) || undefined;
      const pt = raw.payload_template;
      return {
        type: t,
        url: (raw.url as string) || "",
        method: (raw.method as string) || "POST",
        authType: auth?.type || "none",
        authToken: auth?.token || "",
        authHeader: auth?.header || "X-API-Key",
        authKey: auth?.key || "",
        authUser: auth?.username || "",
        authPass: auth?.password || "",
        secret: (raw.secret as string) || "",
        useCustomPayload: !!pt,
        payloadTemplate: pt ? JSON.stringify(pt, null, 2) : "",
        payloadError: "",
      };
    }
    case "broadcast": {
      const pt = raw.payload_template;
      return {
        type: "broadcast",
        useCustomPayload: !!pt,
        payloadTemplate: pt ? JSON.stringify(pt, null, 2) : "",
        payloadError: "",
      };
    }
    case "notify":
      return {
        type: "notify",
        message: (raw.message as string) || "",
        severity: (raw.severity as string) || "info",
      };
    case "email":
      return {
        type: "email",
        to: (raw.to as string) || "",
        subject: (raw.subject as string) || "",
        body: (raw.body as string) || "",
      };
    case "telegram": {
      const existingButtons = Array.isArray(raw.buttons)
        ? (raw.buttons as TelegramButton[])
        : [];
      return {
        type: "telegram",
        channelId: (raw.channel_id as string) || "",
        template: (raw.template as string) || TELEGRAM_DEFAULT_TEMPLATE,
        silent: Boolean(raw.silent),
        includeThumbnail: Boolean(raw.include_thumbnail),
        buttons: existingButtons,
        defaultsApplied: true,
      };
    }
    case "vlm_call":
      return {
        type: "vlm_call",
        provider: (raw.provider as string) || "openai",
        model: (raw.model as string) || "gpt-4o-mini",
        system: (raw.system as string) || "{{defaults.system}}",
        prompt:
          (raw.prompt as string) ||
          "Describe the scene. Focus on people, vehicles, and unusual activity.",
        attachImage: raw.attach_image !== false,
        useSchema: !!raw.response_schema,
        schemaText: raw.response_schema
          ? JSON.stringify(raw.response_schema, null, 2)
          : VLM_SCHEMA_PRESETS.threat,
        output: (raw.output as string) || "result",
        maxRetries: raw.max_retries != null ? String(raw.max_retries) : "1",
        onError: (raw.on_error as string) || "continue",
        timeoutMs: raw.timeout_ms != null ? String(raw.timeout_ms) : "20000",
      };
    case "verify": {
      const mc = raw.min_confidence;
      const onFail = raw.on_fail === "continue" ? "continue" : "stop";
      return {
        type: "verify",
        question: (raw.question as string) || "",
        minConfidence: typeof mc === "number" ? mc : 0.6,
        onFail,
        providerId: (raw.provider_id as string) || "",
      };
    }
  }
  // Fallback. unknown type. coerce to notify.
  return defaultDraftForType("notify");
}

// Convert a draft to the dict shape posted to the backend.
export function draftToDict(d: ActionDraft): Record<string, unknown> {
  if (d.type === "webhook" || d.type === "api_call") {
    const action: Record<string, unknown> = { type: d.type, url: d.url };
    if (d.type === "api_call") action.method = d.method;
    if (d.authType !== "none") {
      const auth: Record<string, string> = { type: d.authType };
      if (d.authType === "bearer") auth.token = d.authToken;
      if (d.authType === "api_key") {
        auth.header = d.authHeader;
        auth.key = d.authKey;
      }
      if (d.authType === "basic") {
        auth.username = d.authUser;
        auth.password = d.authPass;
      }
      action.auth = auth;
    }
    if (d.secret.trim()) action.secret = d.secret.trim();
    if (d.useCustomPayload && d.payloadTemplate.trim()) {
      try {
        action.payload_template = JSON.parse(d.payloadTemplate);
      } catch {
        /* caught at submit validation */
      }
    }
    return action;
  }
  if (d.type === "broadcast") {
    const action: Record<string, unknown> = { type: "broadcast" };
    if (d.useCustomPayload && d.payloadTemplate.trim()) {
      try {
        action.payload_template = JSON.parse(d.payloadTemplate);
      } catch {
        /* caught at submit validation */
      }
    }
    return action;
  }
  if (d.type === "notify") {
    return {
      type: "notify",
      message: d.message || "Rule '{rule_name}' triggered",
      severity: d.severity,
    };
  }
  if (d.type === "email") {
    return {
      type: "email",
      to: d.to,
      subject: d.subject || "Nurby alert. {{rule_name}}",
      body: d.body || "Rule {{rule_name}} fired at {{timestamp}}",
    };
  }
  if (d.type === "telegram") {
    const action: Record<string, unknown> = {
      type: "telegram",
      channel_id: d.channelId,
      template: d.template,
      silent: d.silent,
      include_thumbnail: d.includeThumbnail,
    };
    if (d.buttons.length > 0) {
      action.buttons = d.buttons.map((b) => {
        const out: Record<string, unknown> = { label: b.label, action: b.action };
        if (b.action === "open") out.url = b.url || "{event_url}";
        if (b.action === "mute_event" || b.action === "snooze_rule") {
          if (b.duration_seconds && b.duration_seconds > 0) {
            out.duration_seconds = b.duration_seconds;
          }
        }
        return out;
      });
    }
    return action;
  }
  if (d.type === "vlm_call") {
    const action: Record<string, unknown> = {
      type: "vlm_call",
      provider: d.provider,
      model: d.model,
      system: d.system,
      prompt: d.prompt,
      attach_image: d.attachImage,
      output: d.output || "result",
      max_retries: parseInt(d.maxRetries) || 1,
      on_error: d.onError,
      timeout_ms: parseInt(d.timeoutMs) || 20000,
    };
    if (d.useSchema && d.schemaText.trim()) {
      try {
        action.response_schema = JSON.parse(d.schemaText);
      } catch {
        /* caught at submit */
      }
    }
    return action;
  }
  if (d.type === "verify") {
    const action: Record<string, unknown> = {
      type: "verify",
      question: d.question,
      min_confidence: d.minConfidence,
      on_fail: d.onFail,
    };
    if (d.providerId && d.providerId.trim()) {
      action.provider_id = d.providerId.trim();
    }
    return action;
  }
  // Unreachable. all union members handled above.
  return { type: (d as ActionDraft).type };
}

// Regex mirrors services/events/templates.py `_TOKEN_RE`. Returns
// the dotted paths inside every `{{name.path}}` token in a string.
const VAR_TOKEN_RE = /\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}/g;

export function collectRefsFromValue(value: unknown): string[] {
  const refs: string[] = [];
  const walk = (v: unknown) => {
    if (v == null) return;
    if (typeof v === "string") {
      let m: RegExpExecArray | null;
      VAR_TOKEN_RE.lastIndex = 0;
      while ((m = VAR_TOKEN_RE.exec(v))) refs.push(m[1]);
      return;
    }
    if (Array.isArray(v)) {
      for (const x of v) walk(x);
      return;
    }
    if (typeof v === "object") {
      for (const x of Object.values(v as Record<string, unknown>)) walk(x);
    }
  };
  walk(value);
  return refs;
}

// Walk the chain left-to-right. For each action, collect every
// `{{vars.NAME.*}}` reference and reject if NAME was not declared as
// an `output` by an earlier vlm_call. Mirrors the server-side check in
// `_validate_action_chain`. Returns the index + message of the first
// offending card, or null if the chain is clean.
export function validateActionChainRefs(
  drafts: ActionDraft[],
): { index: number; message: string } | null {
  const known = new Set<string>();
  for (let i = 0; i < drafts.length; i++) {
    const d = drafts[i];
    const dict = draftToDict(d);
    const refs = collectRefsFromValue(dict);
    for (const ref of refs) {
      if (!ref.startsWith("vars.")) continue;
      const tail = ref.slice("vars.".length);
      const name = tail.split(".", 1)[0];
      if (!known.has(name)) {
        return {
          index: i,
          message: `vars.${name} referenced before declaration`,
        };
      }
    }
    if (d.type === "vlm_call" && d.output) {
      known.add(d.output);
    }
  }
  return null;
}

// Available vars an action card at index `i` may reference. Used to
// drive the "Insert var" dropdown. Pulled from prior vlm_call outputs.
export function availableVarsBefore(
  drafts: ActionDraft[],
  index: number,
): { name: string; keys: string[] }[] {
  const out: { name: string; keys: string[] }[] = [];
  for (let i = 0; i < index; i++) {
    const d = drafts[i];
    if (d.type !== "vlm_call" || !d.output) continue;
    const keys: string[] = [];
    if (d.useSchema && d.schemaText.trim()) {
      try {
        const schema = JSON.parse(d.schemaText);
        const props = schema?.properties;
        if (props && typeof props === "object") {
          for (const k of Object.keys(props)) keys.push(k);
        }
      } catch {
        /* ignore */
      }
    }
    out.push({ name: d.output, keys });
  }
  return out;
}

export function buildRuleSummary(rule: Rule, cameras: Camera[]): string {
  const cond = rule.conditions || {};
  const camIds = (cond.camera_ids as string[]) || (cond.camera_id ? [cond.camera_id as string] : []);
  return composeSummary(
    describeTrigger(rule.trigger_pattern),
    resolveCameraNames(camIds, cameras),
    describeSchedule(
      cond.days as string[] | undefined,
      cond.time_after as string | undefined,
      cond.time_before as string | undefined,
    ),
    describeActions(rule.actions),
    rule.cooldown_seconds,
  );
}
