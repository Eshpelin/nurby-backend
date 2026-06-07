"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  ALERT_KINDS,
  Dependant,
  DependantStatus,
  stateColor,
  timeAgo,
} from "@/lib/guardian";

interface TimelineItem {
  observation_id: string;
  at: string;
  zone: string | null;
  camera_name: string | null;
}

export default function DependantDetailPage() {
  const { linkId } = useParams<{ linkId: string }>();
  const { authFetch } = useAuth();
  const [dependant, setDependant] = useState<Dependant | null>(null);
  const [status, setStatus] = useState<DependantStatus | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
      const tRes = await authFetch(`/api/guardian/links/${linkId}/timeline`);
      if (tRes.ok) setTimeline((await tRes.json()).items || []);
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
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold tracking-tight">
            {dependant?.display_name || status?.display_name || "Dependant"}
          </h1>
          <span className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${c.dot}`} />
            <span className={`text-sm ${c.text}`}>{c.label}</span>
          </span>
        </div>
        <div className="mt-3 text-sm">
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
        </div>
        {status?.delayed && (
          <div className="mt-3 inline-block rounded-md bg-amber-950/40 px-2.5 py-1 text-[11px] text-amber-300">
            Free plan. Showing where they were about 30 minutes ago.
          </div>
        )}
      </div>

      {/* Image */}
      <ImagePanel linkId={linkId} canView={!!ent?.can?.image} />

      {/* Timeline */}
      <section className="mt-6">
        <h2 className="text-sm font-medium text-muted-foreground mb-2">Today</h2>
        {timeline.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            No sightings to show yet.
          </div>
        ) : (
          <div className="rounded-lg border border-border bg-card divide-y divide-border">
            {timeline.map((it) => (
              <div key={it.observation_id} className="flex items-center justify-between px-4 py-2.5 text-sm">
                <span>{it.zone || it.camera_name || "Seen"}</span>
                <span className="text-muted-foreground">{timeAgo(it.at)}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Smart search (premium) */}
      {ent?.can?.search && <SearchPanel linkId={linkId} />}

      {/* Alerts */}
      {dependant && (
        <AlertToggles linkId={linkId} initial={dependant.alert_prefs} />
      )}

      {/* Premium upsell */}
      {ent && <UpsellPanel ent={ent} />}
    </div>
  );
}

function ImagePanel({ linkId, canView }: { linkId: string; canView: boolean }) {
  const { token } = useAuth();
  const [src, setSrc] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
        const mins = wait ? Math.ceil(parseInt(wait, 10) / 60) : 60;
        setMsg(`Free plan allows one image per hour. Next image in about ${mins} min.`);
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

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-medium text-muted-foreground">Latest image</h2>
        <button
          onClick={fetchImage}
          disabled={loading}
          className="px-3 py-1 rounded-md border border-border text-xs hover:bg-muted transition-colors disabled:opacity-50"
        >
          {loading ? "Loading..." : src ? "Refresh" : "Show image"}
        </button>
      </div>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="Latest sighting" className="w-full rounded-lg border border-border" />
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
    <section className="mt-6">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">
        Alerts {saving && <span className="text-xs">saving...</span>}
      </h2>
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
    </section>
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

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-amber-400">
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}
