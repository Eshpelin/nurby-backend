"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";

interface StarredStatus {
  person_id: string;
  display_name: string;
  photo_path: string | null;
  status: string;
  last_seen_at: string | null;
  last_camera_id: string | null;
  last_camera_name: string | null;
  last_thumbnail_path: string | null;
  last_observation_id: string | null;
  sightings_24h: number;
  generated_at: string;
  cached: boolean;
  stale: boolean;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "no sightings";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function Avatar({ it, size = "md" }: { it: StarredStatus; size?: "sm" | "md" }) {
  const dim = size === "sm" ? "h-7 w-7 text-[10px]" : "h-8 w-8 text-xs";
  if (it.photo_path) {
    return (
      <img
        src={`/api/persons/${it.person_id}/photo`}
        alt={it.display_name}
        className={`${dim} rounded-full object-cover ring-1 ring-border`}
      />
    );
  }
  return (
    <div className={`${dim} rounded-full bg-muted flex items-center justify-center font-medium ring-1 ring-border`}>
      {it.display_name.charAt(0).toUpperCase()}
    </div>
  );
}

export function StarredStatusRow() {
  const { authFetch, token } = useAuth();
  const [items, setItems] = useState<StarredStatus[]>([]);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const fetchStatus = useCallback(async (force = false) => {
    try {
      const res = await authFetch(`/api/persons/starred/status${force ? "?force=true" : ""}`);
      if (res.ok) setItems(await res.json());
    } catch {
      /* silent */
    }
  }, [authFetch]);

  useEffect(() => {
    fetchStatus();
    const t = setInterval(() => fetchStatus(), 120000);
    return () => clearInterval(t);
  }, [fetchStatus]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const apiBase = process.env.NEXT_PUBLIC_API_BASE || window.location.origin;
    const url = apiBase.replace(/^http/, "ws") + "/ws";
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "recap_stale" || msg.type === "person_seen") {
            fetchStatus();
          }
        } catch {
          /* ignore */
        }
      };
      ws.onerror = () => { /* silent */ };
    } catch {
      /* silent */
    }
    return () => {
      try { wsRef.current?.close(); } catch { /* ignore */ }
    };
  }, [fetchStatus]);

  const refreshOne = useCallback(async (id: string) => {
    setRefreshingId(id);
    try {
      const res = await authFetch(`/api/persons/starred/status?force=true`);
      if (res.ok) setItems(await res.json());
    } finally {
      setRefreshingId(null);
    }
  }, [authFetch]);

  if (items.length === 0) {
    return (
      <div className="flex-shrink-0 mb-3 flex items-center gap-3 px-1 py-1.5">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" className="text-amber-400/80 flex-shrink-0">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
        </svg>
        <span className="text-xs text-muted-foreground">
          No one to watch yet. Star a person to see their recap here.
        </span>
        <a
          href="/people"
          className="ml-auto text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        >
          Open People
        </a>
      </div>
    );
  }

  const active = items.filter((it) => it.last_observation_id && it.last_seen_at);
  const allQuiet = active.length === 0;

  // Quiet mode. One-line flat summary with overlapping avatars. Click to expand.
  if (allQuiet && !expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="flex-shrink-0 mb-3 w-full flex items-center gap-3 px-1 py-1.5 rounded hover:bg-muted/30 transition-colors text-left"
      >
        <div className="flex -space-x-2">
          {items.slice(0, 5).map((it) => (
            <div key={it.person_id} className="ring-2 ring-background rounded-full">
              <Avatar it={it} size="sm" />
            </div>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">
          All quiet. Watching {items.length} {items.length === 1 ? "person" : "people"}.
        </span>
      </button>
    );
  }

  // Active mode. Flat horizontal ribbon, hairline dividers, no outer box.
  return (
    <div className="flex-shrink-0 mb-3">
      <div className="mb-2 flex items-center gap-2 px-1">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" className="text-amber-400">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
        </svg>
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Watching
        </span>
        <span className="text-[10px] text-muted-foreground/60">
          {items.length}
        </span>
        {allQuiet && (
          <button
            onClick={() => setExpanded(false)}
            className="ml-auto text-[11px] text-muted-foreground hover:text-foreground"
          >
            Collapse
          </button>
        )}
      </div>
      <div className="flex divide-x divide-border/60 overflow-x-auto">
        {items.map((it) => {
          const hasSighting = !!it.last_observation_id && !!it.last_seen_at;
          return (
            <div
              key={it.person_id}
              className="flex-shrink-0 w-[280px] flex gap-2.5 px-3 py-2 first:pl-1 group hover:bg-muted/20 transition-colors"
            >
              {hasSighting ? (
                <div className="relative flex-shrink-0 w-16 aspect-video rounded overflow-hidden bg-muted/20">
                  <img
                    src={`/api/observations/${it.last_observation_id}/thumbnail${token ? `?token=${token}` : ""}`}
                    alt=""
                    className="h-full w-full object-cover"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                  />
                </div>
              ) : (
                <div className="flex-shrink-0">
                  <Avatar it={it} />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] font-medium truncate">
                    {it.display_name}
                  </span>
                  {it.stale && (
                    <span className="text-[9px] font-mono text-amber-400">stale</span>
                  )}
                  <button
                    onClick={() => refreshOne(it.person_id)}
                    disabled={refreshingId === it.person_id}
                    aria-label="Refresh recap"
                    className="ml-auto p-0.5 rounded text-muted-foreground/60 hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-40"
                  >
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      className={refreshingId === it.person_id ? "animate-spin" : ""}
                    >
                      <polyline points="23 4 23 10 17 10" />
                      <polyline points="1 20 1 14 7 14" />
                      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                    </svg>
                  </button>
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {hasSighting ? (
                    <>
                      {timeAgo(it.last_seen_at)}
                      {it.last_camera_name && (
                        <>
                          <span className="mx-1">&middot;</span>
                          {it.last_camera_name}
                        </>
                      )}
                      {it.sightings_24h > 0 && (
                        <>
                          <span className="mx-1">&middot;</span>
                          {it.sightings_24h}x today
                        </>
                      )}
                    </>
                  ) : (
                    "no recent sightings"
                  )}
                </div>
                <p className="mt-1 text-[11px] leading-snug text-foreground/80 line-clamp-2">
                  {it.status}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
