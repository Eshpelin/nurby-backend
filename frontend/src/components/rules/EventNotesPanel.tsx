"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

// Phase 4. Inline notes panel rendered under each expanded event.
// Lets the household leave free-text annotations. Telegram-sourced
// notes show a small pill so the operator can correlate.
interface EventNote {
  id: string;
  event_id: string;
  author_user_id: string | null;
  author_display_name: string | null;
  source: "telegram" | "web" | "api";
  text: string;
  telegram_message_id: number | null;
  created_at: string;
}

export function EventNotesPanel({ eventId }: { eventId: string }) {
  const { authFetch } = useAuth();
  const [notes, setNotes] = useState<EventNote[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`/api/events/${eventId}/notes`);
      if (res.ok) setNotes(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch, eventId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const submit = async () => {
    const text = draft.trim();
    if (!text) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await authFetch(`/api/events/${eventId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.detail || `Could not save (${res.status}).`);
        return;
      }
      setDraft("");
      await refresh();
    } catch {
      setError("Network error saving note.");
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (noteId: string) => {
    try {
      const res = await authFetch(`/api/events/${eventId}/notes/${noteId}`, {
        method: "DELETE",
      });
      if (res.ok || res.status === 204) await refresh();
    } catch {
      /* silent */
    }
  };

  return (
    <div className="mt-3 pt-3 border-t border-border">
      <div className="text-[10px] text-muted-foreground mb-1">Notes</div>
      {loading && notes.length === 0 && (
        <div className="text-[11px] text-muted-foreground">Loading.</div>
      )}
      {!loading && notes.length === 0 && (
        <div className="text-[11px] text-muted-foreground">No notes yet.</div>
      )}
      {notes.length > 0 && (
        <ul className="space-y-1.5">
          {notes.map((n) => (
            <li key={n.id} className="text-[11px] flex items-start gap-2">
              <span
                className={`px-1 py-0.5 rounded text-[9px] uppercase border ${
                  n.source === "telegram"
                    ? "border-blue-500/40 text-blue-400 bg-blue-500/10"
                    : n.source === "web"
                    ? "border-border text-muted-foreground bg-muted/40"
                    : "border-border text-muted-foreground bg-muted/40"
                }`}
                title={`Source. ${n.source}`}
              >
                {n.source}
              </span>
              <div className="flex-1">
                <div className="whitespace-pre-wrap text-foreground/90">{n.text}</div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {n.author_display_name || "Unknown"} ·{" "}
                  {new Date(n.created_at).toLocaleString()}
                </div>
              </div>
              <button
                type="button"
                onClick={() => remove(n.id)}
                className="text-[10px] text-muted-foreground hover:text-red-400"
                title="Delete note"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex items-start gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="+ Add note"
          rows={2}
          className="flex-1 px-2 py-1.5 rounded-md bg-background border border-border text-[11px] resize-y"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={submitting || !draft.trim()}
          className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted disabled:opacity-50"
        >
          {submitting ? "Saving." : "Save"}
        </button>
      </div>
      {error && (
        <div className="mt-1 text-[10px] text-red-400">{error}</div>
      )}
    </div>
  );
}
