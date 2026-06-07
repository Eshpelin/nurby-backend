// Shared types + helpers for the Guardian Panel.

export interface Entitlements {
  tier: string;
  delayed: boolean;
  premium: boolean;
  live_presence: boolean;
  live_video: boolean;
  audio: boolean;
  can: Record<string, boolean>;
}

export interface Dependant {
  link_id: string;
  person_id: string;
  display_name: string | null;
  relationship_label: string | null;
  active: boolean;
  expires_at: string | null;
  has_photo: boolean;
  photo_url: string | null;
  alert_prefs: Record<string, boolean>;
  notify_channels: Record<string, boolean>;
  entitlements: Entitlements;
}

export interface GuardianEvent {
  id: string;
  kind: string;
  message: string;
  severity: string;
  zone: string | null;
  at: string;
  pickup_matched: boolean | null;
  pickup_name: string | null;
}

export const EVENT_META: Record<string, { label: string; dot: string }> = {
  arrived: { label: "Arrived", dot: "bg-emerald-500" },
  departed: { label: "Left", dot: "bg-zinc-400" },
  picked_up: { label: "Picked up", dot: "bg-emerald-500" },
  entered_zone: { label: "Entered", dot: "bg-sky-500" },
  left_zone: { label: "Left zone", dot: "bg-zinc-400" },
  not_seen: { label: "Not seen", dot: "bg-amber-500" },
};

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yest.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export const NOTIFY_CHANNELS: { key: string; label: string }[] = [
  { key: "telegram", label: "Telegram" },
  { key: "email", label: "Email" },
  { key: "in_app", label: "In-app" },
];

export interface DependantStatus {
  state: "present" | "away" | "unknown";
  last_seen_at: string | null;
  seconds_ago: number | null;
  delayed: boolean;
  as_of: string;
  zone: string | null;
  camera_name: string | null;
  display_name: string;
  observation_id: string | null;
  entitlements: Entitlements;
}

export const ALERT_KINDS: { key: string; label: string }[] = [
  { key: "arrived", label: "Arrived safely" },
  { key: "departed", label: "Left / departed" },
  { key: "picked_up", label: "Picked up" },
  { key: "entered_zone", label: "Entered a zone" },
  { key: "left_zone", label: "Left a zone" },
  { key: "not_seen", label: "Not seen for a while" },
];

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export function stateColor(state: string): { dot: string; text: string; label: string } {
  switch (state) {
    case "present":
      return { dot: "bg-emerald-500", text: "text-emerald-400", label: "Present" };
    case "away":
      return { dot: "bg-amber-500", text: "text-amber-400", label: "Away" };
    default:
      return { dot: "bg-zinc-500", text: "text-zinc-400", label: "Not seen" };
  }
}
