"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Subscription {
  id: string;
  name: string;
  url: string;
  active: boolean;
  has_secret: boolean;
  rule_ids: string[] | null;
  camera_ids: string[] | null;
  last_delivery_at: string | null;
  last_status: string | null;
  created_at: string;
}

// Standing outbound webhooks. Independent of any single rule, they
// receive every fired event. Listed and managed here so the capability
// is discoverable from the Rules page.
export function WebhookSubscriptions() {
  const { authFetch } = useAuth();
  const [open, setOpen] = useState(false);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch("/api/webhook-subscriptions");
      if (res.ok) setSubs(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const create = async () => {
    if (!name.trim() || !url.trim()) {
      setError("Name and URL are required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await authFetch("/api/webhook-subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), url: url.trim(), secret: secret.trim() || null }),
      });
      if (res.status === 403) {
        setError("Admin access required to add a subscription");
        return;
      }
      if (!res.ok) {
        setError("Failed to create subscription");
        return;
      }
      setName("");
      setUrl("");
      setSecret("");
      setShowForm(false);
      load();
    } catch {
      setError("Network error");
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (s: Subscription) => {
    setSubs((prev) => prev.map((x) => (x.id === s.id ? { ...x, active: !s.active } : x)));
    try {
      await authFetch(`/api/webhook-subscriptions/${s.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !s.active }),
      });
    } catch {
      load();
    }
  };

  const remove = async (s: Subscription) => {
    setSubs((prev) => prev.filter((x) => x.id !== s.id));
    try {
      await authFetch(`/api/webhook-subscriptions/${s.id}`, { method: "DELETE" });
    } catch {
      load();
    }
  };

  return (
    <div className="mt-8 border border-border rounded-lg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/40 rounded-lg"
      >
        <div className="text-left">
          <div className="text-sm font-medium">Webhook subscribers</div>
          <div className="text-[11px] text-muted-foreground">
            Standing endpoints that receive every fired event. Signed and retried.
          </div>
        </div>
        <span className="text-muted-foreground text-xs">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {loading && <div className="text-[11px] text-muted-foreground">Loading.</div>}

          {!loading && subs.length === 0 && (
            <div className="text-[11px] text-muted-foreground">No subscribers yet.</div>
          )}

          {subs.map((s) => (
            <div
              key={s.id}
              className="flex items-center justify-between gap-3 border border-border rounded-md px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium truncate">{s.name}</span>
                  {s.has_secret && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground">
                      signed
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground font-mono truncate">{s.url}</div>
                {s.last_status && (
                  <div className="text-[10px] text-muted-foreground">last. {s.last_status}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => toggleActive(s)}
                  className={`text-[10px] px-2 py-1 rounded border transition-colors ${
                    s.active
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-border text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {s.active ? "Active" : "Paused"}
                </button>
                <button
                  type="button"
                  onClick={() => remove(s)}
                  className="text-[10px] px-2 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}

          {showForm ? (
            <div className="border border-border rounded-md p-3 space-y-2">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Name (e.g. home-hub)"
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
              />
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://your-host/nurby-events"
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
              />
              <input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Signing secret (optional, HMAC-SHA256)"
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
              />
              {error && <div className="text-[11px] text-red-400">{error}</div>}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowForm(false);
                    setError("");
                  }}
                  className="px-2 py-1 text-xs rounded border border-border hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={create}
                  disabled={saving}
                  className="px-3 py-1 text-xs rounded bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "Adding." : "Add subscriber"}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="px-2 py-1 text-xs rounded border border-dashed border-border hover:bg-muted text-muted-foreground"
            >
              + Add subscriber
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default WebhookSubscriptions;
