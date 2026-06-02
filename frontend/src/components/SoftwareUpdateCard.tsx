"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface VersionInfo {
  current: string;
  build: string;
  latest: string | null;
  release_url: string | null;
  update_available: boolean;
  self_update_enabled: boolean;
  repo: string;
  error: string | null;
}

// Shows the running version, checks GitHub for a newer release, and
// offers an update path. one-click when the optional updater sidecar is
// enabled, otherwise the manual command.
export function SoftwareUpdateCard() {
  const { authFetch } = useAuth();
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [updateMsg, setUpdateMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await authFetch("/api/system/version");
      if (r.ok) setInfo(await r.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    load();
  }, [load]);

  const triggerUpdate = async () => {
    setUpdating(true);
    setUpdateMsg("");
    try {
      const r = await authFetch("/api/system/update", { method: "POST" });
      if (r.status === 403) {
        setUpdateMsg("Admin access is required to update.");
        return;
      }
      const j = await r.json().catch(() => ({}));
      setUpdateMsg(j.message || "Update requested.");
    } catch {
      setUpdateMsg("Could not reach the server.");
    } finally {
      setUpdating(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Software updates</h2>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {loading
              ? "Checking for updates."
              : info
              ? `Version ${info.current}${info.build ? ` (${info.build.slice(0, 7)})` : ""}`
              : "Version unknown"}
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
        >
          Check again
        </button>
      </div>

      {info && (
        <div className="mt-3 space-y-2">
          {info.error && (
            <div className="text-[11px] text-amber-300/90">{info.error}</div>
          )}

          {!info.error && !info.update_available && info.latest && (
            <div className="text-[11px] text-emerald-300/90">
              You are on the latest release.
            </div>
          )}

          {info.update_available && (
            <div className="rounded-md border border-accent/30 bg-accent/5 px-3 py-2.5 space-y-2">
              <div className="text-xs">
                <span className="font-medium text-accent">Update available.</span>{" "}
                {info.current} {"->"} {info.latest}
                {info.release_url && (
                  <>
                    {" . "}
                    <a
                      href={info.release_url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline text-accent"
                    >
                      release notes
                    </a>
                  </>
                )}
              </div>

              {info.self_update_enabled ? (
                <button
                  type="button"
                  onClick={triggerUpdate}
                  disabled={updating}
                  className="px-3 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
                >
                  {updating ? "Starting." : "Update now"}
                </button>
              ) : (
                <div className="text-[11px] text-muted-foreground">
                  To update, run this on the host that runs Nurby.
                  <pre className="mt-1 px-2 py-1.5 rounded bg-background border border-border font-mono text-[11px] overflow-x-auto">
                    ./scripts/update.sh
                  </pre>
                  One-click updates can be enabled with the optional updater service.
                  see docs/updating.md.
                </div>
              )}

              {updateMsg && (
                <div className="text-[11px] text-muted-foreground">{updateMsg}</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default SoftwareUpdateCard;
