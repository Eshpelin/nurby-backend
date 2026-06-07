"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Dependant, DependantStatus, stateColor, timeAgo } from "@/lib/guardian";

// The 10-second check. One calm status card per dependant. Most sessions end
// here. Free tier shows a "as of 30 min ago" note; nothing is invented.
export default function GuardianPage() {
  const { user, authFetch } = useAuth();
  const [dependants, setDependants] = useState<Dependant[]>([]);
  const [statuses, setStatuses] = useState<Record<string, DependantStatus>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await authFetch("/api/guardian/me");
      if (!res.ok) throw new Error("Could not load your dependants.");
      const data = await res.json();
      const deps: Dependant[] = data.dependants || [];
      setDependants(deps);
      // Pull a status for each active dependant.
      const entries = await Promise.all(
        deps
          .filter((d) => d.active)
          .map(async (d) => {
            try {
              const r = await authFetch(`/api/guardian/links/${d.link_id}/status`);
              if (!r.ok) return null;
              return [d.link_id, (await r.json()) as DependantStatus] as const;
            } catch {
              return null;
            }
          })
      );
      const map: Record<string, DependantStatus> = {};
      for (const e of entries) if (e) map[e[0]] = e[1];
      setStatuses(map);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) {
    return <div className="p-8 text-muted-foreground">Loading your dependants...</div>;
  }

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Guardian</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Peace of mind, nothing more. You only ever see the people you are bound to.
          </p>
        </div>
        {user?.role === "admin" && (
          <Link
            href="/guardian/admin"
            className="px-3 py-1.5 rounded-md border border-border text-sm hover:bg-muted transition-colors"
          >
            Manage access
          </Link>
        )}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {dependants.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <p className="text-muted-foreground">
            You are not following anyone yet.
          </p>
          {user?.role === "admin" && (
            <p className="text-sm text-muted-foreground mt-2">
              Use{" "}
              <Link href="/guardian/admin" className="text-emerald-400 hover:underline">
                Manage access
              </Link>{" "}
              to bind a guardian to a person.
            </p>
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {dependants.map((d) => (
            <DependantCard key={d.link_id} dependant={d} status={statuses[d.link_id]} />
          ))}
        </div>
      )}
    </div>
  );
}

function DependantCard({
  dependant,
  status,
}: {
  dependant: Dependant;
  status?: DependantStatus;
}) {
  if (!dependant.active) {
    return (
      <div className="rounded-lg border border-border bg-card p-5 opacity-60">
        <div className="font-medium">{dependant.display_name}</div>
        <div className="text-sm text-muted-foreground mt-1">Access ended.</div>
      </div>
    );
  }
  const st = status?.state || "unknown";
  const c = stateColor(st);
  return (
    <Link
      href={`/guardian/${dependant.link_id}`}
      className="block rounded-lg border border-border bg-card p-5 hover:border-zinc-600 transition-colors"
    >
      <div className="flex items-center justify-between">
        <div className="font-medium">{dependant.display_name}</div>
        <span className="flex items-center gap-1.5 text-xs">
          <span className={`h-2 w-2 rounded-full ${c.dot}`} />
          <span className={c.text}>{c.label}</span>
        </span>
      </div>
      {dependant.relationship_label && (
        <div className="text-xs text-muted-foreground mt-0.5 capitalize">
          {dependant.relationship_label}
        </div>
      )}
      <div className="mt-4 text-sm">
        {st === "unknown" ? (
          <span className="text-muted-foreground">No recent sighting.</span>
        ) : (
          <span>
            {status?.zone ? (
              <span className="text-foreground">{status.zone}</span>
            ) : (
              <span className="text-muted-foreground">Location unknown</span>
            )}
            <span className="text-muted-foreground">
              {" "}
              · seen {timeAgo(status?.last_seen_at || null)}
            </span>
          </span>
        )}
      </div>
      {status?.delayed && (
        <div className="mt-3 text-[11px] text-amber-400/80">
          Showing data as of 30 minutes ago. Upgrade for live presence.
        </div>
      )}
    </Link>
  );
}
