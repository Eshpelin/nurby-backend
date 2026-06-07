"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { DependantAvatar } from "@/components/guardian-avatar";
import {
  ALERT_KINDS,
  clockTime,
  dayLabel,
  Dependant,
  DependantStatus,
  EVENT_META,
  GuardianEvent,
  NOTIFY_CHANNELS,
  stateColor,
  timeAgo,
} from "@/lib/guardian";

export default function DependantDetailPage() {
  const { linkId } = useParams<{ linkId: string }>();
  const { authFetch } = useAuth();
  const [dependant, setDependant] = useState<Dependant | null>(null);
  const [status, setStatus] = useState<DependantStatus | null>(null);
  const [events, setEvents] = useState<GuardianEvent[]>([]);
  const [lastPickup, setLastPickup] = useState<GuardianEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showTrust, setShowTrust] = useState(false);

  // Show the "what you can and cannot see" screen once, on first open.
  useEffect(() => {
    try {
      if (!localStorage.getItem("guardian_trust_ack")) setShowTrust(true);
    } catch {
      // ignore
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const meRes = await authFetch("/api/guardian/me");
      if (meRes.ok) {
        const me = await meRes.json();
        const dep = (me.dependants || []).find(
          (d: Dependant) => d.link_id === linkId
        );
        setDependant(dep || null);
      }
      const sRes = await authFetch(`/api/guardian/links/${linkId}/status`);
      if (sRes.ok) setStatus(await sRes.json());
      else if (sRes.status === 410) setError("This guardian link is no longer active.");
      const eRes = await authFetch(`/api/guardian/links/${linkId}/events`);
      if (eRes.ok) {
        const data = await eRes.json();
        setEvents(data.items || []);
        setLastPickup(data.last_pickup || null);
      }
    } catch {
      setError("Could not load this dependant.");
    } finally {
      setLoading(false);
    }
  }, [authFetch, linkId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return <div className="p-8 text-muted-foreground">Loading...</div>;

  const st = status?.state || "unknown";
  const c = stateColor(st);
  const ent = dependant?.entitlements;

  return (
    <div className="max-w-3xl mx-auto p-6">
      <Link href="/guardian" className="text-sm text-muted-foreground hover:text-foreground">
        ← All dependants
      </Link>

      {error && (
        <div className="my-4 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Status header */}
      <div className="mt-4 rounded-lg border border-border bg-card p-6">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <DependantAvatar
              photoUrl={dependant?.photo_url ?? null}
              name={dependant?.display_name || status?.display_name || null}
              size={52}
            />
            <h1 className="text-2xl font-semibold tracking-tight truncate">
              {dependant?.display_name || status?.display_name || "Dependant"}
            </h1>
          </div>
          <span className="flex items-center gap-2 shrink-0">
            <span className={`h-2.5 w-2.5 rounded-full ${c.dot}`} />
            <span className={`text-sm ${c.text}`}>{c.label}</span>
          </span>
        </div>
        <div className="mt-3 text-sm flex items-center gap-2 flex-wrap">
          {st === "unknown" ? (
            <span className="text-muted-foreground">No recent sighting. Nothing to show yet.</span>
          ) : (
            <span>
              {status?.zone ? (
                <span className="text-foreground">{status.zone}</span>
              ) : (
                <span className="text-muted-foreground">Location unknown</span>
              )}
              <span className="text-muted-foreground">
                {" "}· last seen {timeAgo(status?.last_seen_at || null)}
              </span>
            </span>
          )}
          {status?.delayed && <AsOfChip />}
        </div>
      </div>

      {/* Pickup moment. The highest-value event gets its own warm card. */}
      {lastPickup && <PickupMomentCard event={lastPickup} />}

      {/* Image */}
      <ImagePanel linkId={linkId} canView={!!ent?.can?.image} />

      {/* Day-timeline. real arrival/pickup/zone events, grouped by day. */}
      <EventTimeline events={events} />

      {/* Weekly trends (premium) */}
      {ent?.premium && <TrendsPanel linkId={linkId} />}

      {/* Smart search (premium) */}
      {ent?.can?.search && <SearchPanel linkId={linkId} />}

      {/* Notifications: what alerts, and how they reach you. */}
      {dependant && (
        <section className="mt-6">
          <h2 className="text-sm font-medium text-muted-foreground mb-2">Notifications</h2>
          <AlertToggles linkId={linkId} initial={dependant.alert_prefs} />
          <div className="h-3" />
          <ChannelToggles
            linkId={linkId}
            initial={dependant.notify_channels || { telegram: true, email: true, in_app: true }}
          />
        </section>
      )}

      {/* Premium upsell */}
      {ent && <UpsellPanel ent={ent} />}

      <div className="mt-8 text-center">
        <button
          onClick={() => setShowTrust(true)}
          className="text-xs text-muted-foreground hover:text-foreground underline"
        >
          What can I see, and what stays private?
        </button>
      </div>

      {showTrust && (
        <TrustModal
          onClose={() => {
            try {
              localStorage.setItem("guardian_trust_ack", "1");
            } catch {
              // ignore
            }
            setShowTrust(false);
          }}
        />
      )}
    </div>
  );
}

function ImagePanel({ linkId, canView }: { linkId: string; canView: boolean }) {
  const { token } = useAuth();
  const [src, setSrc] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0); // seconds until next free image

  // Live countdown for the throttle.
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setInterval(() => setCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [cooldown]);

  const fetchImage = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/guardian/links/${linkId}/image`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const blob = await res.blob();
        setSrc(URL.createObjectURL(blob));
      } else if (res.status === 429) {
        const wait = res.headers.get("Retry-After");
        setCooldown(wait ? parseInt(wait, 10) : 3600);
        setMsg(null);
      } else if (res.status === 404) {
        setMsg("No recent image available.");
      } else {
        setMsg("Image not available on your plan.");
      }
    } catch {
      setMsg("Could not load the image.");
    } finally {
      setLoading(false);
    }
  }, [linkId, token]);

  if (!canView) return null;

  const mm = Math.floor(cooldown / 60);
  const ss = String(cooldown % 60).padStart(2, "0");

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-medium text-muted-foreground">Latest image</h2>
        <button
          onClick={fetchImage}
          disabled={loading || cooldown > 0}
          className="px-3 py-1 rounded-md border border-border text-xs hover:bg-muted transition-colors disabled:opacity-50"
        >
          {loading ? "Loading..." : cooldown > 0 ? `${mm}:${ss}` : src ? "Refresh" : "Show image"}
        </button>
      </div>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="Latest sighting" className="w-full rounded-lg border border-border" />
      ) : cooldown > 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card p-6 text-center text-sm">
          <div className="text-muted-foreground">
            Free plan allows one image per hour. Next image in{" "}
            <span className="text-foreground tabular-nums">
              {mm}:{ss}
            </span>
            .
          </div>
          <div className="text-[11px] text-emerald-400 mt-1">Upgrade for unlimited images.</div>
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border bg-card p-6 text-center text-sm text-muted-foreground">
          {msg || "Tap Show image to load the most recent blurred snapshot."}
        </div>
      )}
    </div>
  );
}

function AlertToggles({
  linkId,
  initial,
}: {
  linkId: string;
  initial: Record<string, boolean>;
}) {
  const { authFetch } = useAuth();
  const [prefs, setPrefs] = useState<Record<string, boolean>>(initial);
  const [saving, setSaving] = useState(false);

  const toggle = async (key: string) => {
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    setSaving(true);
    try {
      const res = await authFetch(`/api/guardian/links/${linkId}/alerts`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_prefs: next }),
      });
      if (res.ok) setPrefs((await res.json()).alert_prefs);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1.5">
        What to tell me {saving && <span>saving...</span>}
      </div>
      <div className="rounded-lg border border-border bg-card divide-y divide-border">
        {ALERT_KINDS.map((a) => (
          <label
            key={a.key}
            className="flex items-center justify-between px-4 py-3 text-sm cursor-pointer"
          >
            <span>{a.label}</span>
            <button
              type="button"
              onClick={() => toggle(a.key)}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                prefs[a.key] ? "bg-emerald-600" : "bg-zinc-700"
              }`}
            >
              <span
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  prefs[a.key] ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </label>
        ))}
      </div>
    </div>
  );
}

function ChannelToggles({
  linkId,
  initial,
}: {
  linkId: string;
  initial: Record<string, boolean>;
}) {
  const { authFetch } = useAuth();
  const [channels, setChannels] = useState<Record<string, boolean>>(initial);
  const [saving, setSaving] = useState(false);

  const toggle = async (key: string) => {
    const next = { ...channels, [key]: !channels[key] };
    setChannels(next);
    setSaving(true);
    try {
      const res = await authFetch(`/api/guardian/links/${linkId}/channels`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notify_channels: next }),
      });
      if (res.ok) setChannels((await res.json()).notify_channels);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1.5">
        How to reach me {saving && <span>saving...</span>}
      </div>
      <div className="rounded-lg border border-border bg-card divide-y divide-border">
        {NOTIFY_CHANNELS.map((ch) => (
          <label
            key={ch.key}
            className="flex items-center justify-between px-4 py-3 text-sm cursor-pointer"
          >
            <span>{ch.label}</span>
            <button
              type="button"
              onClick={() => toggle(ch.key)}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                channels[ch.key] ? "bg-emerald-600" : "bg-zinc-700"
              }`}
            >
              <span
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  channels[ch.key] ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </label>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground mt-2">
        Telegram needs a paired bot. Email goes to your account address. In-app always shows here.
      </p>
    </div>
  );
}

function UpsellPanel({ ent }: { ent: Dependant["entitlements"] }) {
  const locked: { label: string; desc: string }[] = [];
  if (ent.delayed)
    locked.push({ label: "Live presence", desc: "See where they are right now, not 30 minutes ago." });
  if (!ent.live_video)
    locked.push({ label: "Live video", desc: "Short blurred live clips, on demand." });
  if (!ent.audio)
    locked.push({ label: "Audio signals", desc: "Surface audio events for added context." });
  if (!ent.premium)
    locked.push({ label: "Daily recap", desc: "A warm daily summary of their day." });

  if (locked.length === 0) return null;

  return (
    <section className="mt-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">Upgrade</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {locked.map((u) => (
          <div key={u.label} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2">
              <LockIcon />
              <span className="font-medium text-sm">{u.label}</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1.5">{u.desc}</p>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground mt-3">
        Paid plans are not yet available. These will unlock when billing launches.
      </p>
    </section>
  );
}

function SearchPanel({ linkId }: { linkId: string }) {
  const { authFetch } = useAuth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<
    { observation_id: string; at: string; zone: string | null; caption: string | null }[]
  >([]);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const res = await authFetch(`/api/guardian/links/${linkId}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, limit: 20 }),
      });
      if (res.ok) {
        setResults((await res.json()).results || []);
        setSearched(true);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">Ask about their day</h2>
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="outdoor, lunch, classroom..."
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
        <button
          onClick={run}
          disabled={busy}
          className="px-4 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm transition-colors disabled:opacity-50"
        >
          {busy ? "..." : "Search"}
        </button>
      </div>
      {searched && (
        <div className="mt-3 rounded-lg border border-border bg-card divide-y divide-border">
          {results.length === 0 ? (
            <div className="px-4 py-3 text-sm text-muted-foreground">No matches.</div>
          ) : (
            results.map((r) => (
              <div key={r.observation_id} className="px-4 py-2.5 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-foreground">{r.zone || "Seen"}</span>
                  <span className="text-muted-foreground text-xs">{timeAgo(r.at)}</span>
                </div>
                {r.caption && (
                  <div className="text-xs text-muted-foreground mt-1">{r.caption}</div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}

function AsOfChip() {
  return (
    <span className="inline-flex items-center rounded-md bg-amber-950/40 px-2 py-0.5 text-[11px] text-amber-300">
      as of ~30 min ago
    </span>
  );
}

function PickupMomentCard({ event }: { event: GuardianEvent }) {
  const matched = event.pickup_matched;
  return (
    <div
      className={`mt-4 rounded-lg border p-5 ${
        matched === false
          ? "border-amber-800 bg-amber-950/20"
          : "border-emerald-800 bg-emerald-950/20"
      }`}
    >
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
        <span className={`h-2 w-2 rounded-full ${matched === false ? "bg-amber-500" : "bg-emerald-500"}`} />
        {matched === false ? "Unrecognized pickup" : "Picked up"}
      </div>
      <div className="mt-1.5 text-lg font-medium">{event.message}</div>
      <div className="mt-1 text-xs text-muted-foreground">
        {clockTime(event.at)} · {dayLabel(event.at)}
      </div>
    </div>
  );
}

function EventTimeline({ events }: { events: GuardianEvent[] }) {
  // Group by day, newest first.
  const groups: { day: string; items: GuardianEvent[] }[] = [];
  for (const e of events) {
    const day = dayLabel(e.at);
    const g = groups.find((x) => x.day === day);
    if (g) g.items.push(e);
    else groups.push({ day, items: [e] });
  }

  return (
    <section className="mt-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">Their day</h2>
      {events.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
          No events yet. Arrival, pickup, and zone moments will appear here.
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((g) => (
            <div key={g.day}>
              <div className="text-xs text-muted-foreground mb-1.5">{g.day}</div>
              <div className="relative pl-4 border-l border-border space-y-3">
                {g.items.map((e) => {
                  const meta = EVENT_META[e.kind] || { label: e.kind, dot: "bg-zinc-500" };
                  return (
                    <div key={e.id} className="relative">
                      <span
                        className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ${meta.dot} ring-2 ring-background`}
                      />
                      <div className="text-sm">{e.message}</div>
                      <div className="text-[11px] text-muted-foreground">{clockTime(e.at)}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

interface TrendDay {
  date: string;
  sightings: number;
  first_seen: string | null;
  last_seen: string | null;
  zones: string[];
}

function TrendsPanel({ linkId }: { linkId: string }) {
  const { authFetch } = useAuth();
  const [data, setData] = useState<{ days_seen: number; total_sightings: number; days: TrendDay[] } | null>(
    null
  );

  const load = useCallback(async () => {
    try {
      const r = await authFetch(`/api/guardian/links/${linkId}/trends`);
      if (r.ok) setData(await r.json());
    } catch {
      // ignore
    }
  }, [authFetch, linkId]);

  useEffect(() => {
    // load() only setStates after the awaited fetch resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (!data || data.days.length === 0) return null;

  return (
    <section className="mt-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">This week</h2>
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex gap-6 text-sm">
          <div>
            <div className="text-xl font-semibold">{data.days_seen}</div>
            <div className="text-xs text-muted-foreground">days seen</div>
          </div>
          <div>
            <div className="text-xl font-semibold">{data.total_sightings}</div>
            <div className="text-xs text-muted-foreground">sightings</div>
          </div>
        </div>
        <div className="mt-3 space-y-1">
          {data.days.map((d) => (
            <div key={d.date} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                {new Date(d.date).toLocaleDateString(undefined, { weekday: "short", day: "numeric" })}
              </span>
              <span>
                {d.first_seen ? clockTime(d.first_seen) : "-"} to{" "}
                {d.last_seen ? clockTime(d.last_seen) : "-"}
              </span>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground mt-2">
          Gentle wellbeing signals, not judgments.
        </p>
      </div>
    </section>
  );
}

function TrustModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="max-w-md w-full rounded-lg border border-border bg-card p-6">
        <h2 className="text-lg font-semibold">What you can see</h2>
        <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
          <li>Whether your dependant is present, and where, in plain words.</li>
          <li>Arrival, pickup, and zone moments as they happen.</li>
          <li>Recent images, blurred so no one is identifiable by face.</li>
        </ul>
        <h2 className="text-lg font-semibold mt-4">What stays private</h2>
        <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
          <li>Every other person stays blurred and anonymous. Always.</li>
          <li>You only ever see the people you are bound to.</li>
          <li>Free plans are delayed by about 30 minutes.</li>
          <li>Every view you make is logged and visible to the facility.</li>
        </ul>
        <p className="mt-4 text-xs text-muted-foreground">
          This is an awareness aid, not a guarantee of safety.
        </p>
        <button
          onClick={onClose}
          className="mt-5 w-full rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-sm py-2 transition-colors"
        >
          I understand
        </button>
      </div>
    </div>
  );
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-amber-400">
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}
