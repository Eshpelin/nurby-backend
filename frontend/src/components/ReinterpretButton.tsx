"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Provider {
  id: string;
  name: string;
  kind: string;
  default_model: string | null;
}

interface Props {
  // POST endpoint that accepts { provider_id }.
  endpoint: string;
  // Label override (e.g. "Reinterpret conversation").
  label?: string;
  // Compact variant for inline use inside dense cards.
  variant?: "default" | "compact";
  // Optional callback when reinterpretation succeeds. Parent can
  // refresh state.
  onSuccess?: () => void;
}

/**
 * Reinterpret control. Opens a small popover with a provider picker
 * and re-runs the underlying VLM call with the chosen model. Lets
 * the user ask "what would Claude make of this?" on a journey
 * already narrated by local Gemma, or fall back to "use default" if
 * they just want a fresh take from the same provider.
 *
 * Backwards-compatible. When the endpoint is hit with no body the
 * old camera-precedence chain still runs (existing /resummarize
 * behavior).
 */
export function ReinterpretButton({
  endpoint,
  label = "Reinterpret",
  variant = "default",
  onSuccess,
}: Props) {
  const { authFetch } = useAuth();
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [picked, setPicked] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const loadProviders = useCallback(async () => {
    try {
      const res = await authFetch("/api/providers");
      if (res.ok) {
        setProviders(await res.json());
      }
    } catch {
      /* ignore */
    }
  }, [authFetch]);

  useEffect(() => {
    if (open && providers.length === 0) loadProviders();
  }, [open, providers.length, loadProviders]);

  const run = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const body: Record<string, unknown> = {};
      if (picked) body.provider_id = picked;
      const res = await authFetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `status ${res.status}`);
      }
      setDone(true);
      onSuccess?.();
      setTimeout(() => {
        setDone(false);
        setOpen(false);
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const triggerClass =
    variant === "compact"
      ? "px-2 py-1 text-[10px] rounded text-muted-foreground hover:text-violet-300 hover:bg-violet-500/10"
      : "px-2 py-1 text-xs rounded border border-violet-500/30 text-violet-300 hover:bg-violet-500/10";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={triggerClass}
        title="Re-run with the model of your choice"
      >
        ✨ {label}
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-30 w-64 rounded-md border border-border bg-card shadow-lg p-3 space-y-2"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Pick a model
          </div>
          <select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            className="w-full px-2 py-1.5 text-xs rounded border border-border bg-background"
          >
            <option value="">(camera default chain)</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.default_model ? ` · ${p.default_model}` : ""}
              </option>
            ))}
          </select>
          <p className="text-[10px] text-muted-foreground leading-relaxed">
            The chosen model gets the full source data
            (transcripts, observations, segments) and produces a
            fresh interpretation. Existing narration stays in the
            history.
          </p>
          {error && (
            <p className="text-[11px] text-danger">{error}</p>
          )}
          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="px-2 py-1 text-xs rounded text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={run}
              disabled={busy}
              className="px-2 py-1 text-xs rounded bg-violet-500/20 text-violet-200 border border-violet-500/40 hover:bg-violet-500/30 disabled:opacity-50"
            >
              {busy ? "Running." : done ? "Done" : "Re-interpret"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
