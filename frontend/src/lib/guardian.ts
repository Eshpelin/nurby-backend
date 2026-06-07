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
  alert_prefs: Record<string, boolean>;
  entitlements: Entitlements;
}

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
