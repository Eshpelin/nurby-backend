"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useWSSubscribe } from "@/lib/ws";
import { ReinterpretButton } from "@/components/ReinterpretButton";

interface ConversationTranscript {
  id: string;
  started_at: string;
  ended_at: string;
  text: string;
  language?: string | null;
  provider?: string | null;
  audio_capture_id?: string | null;
  speaker_person_id?: string | null;
  speaker_source?: string | null;
}

interface ConversationCardProps {
  id: string;
  cameraId: string;
  cameraName?: string;
  startedAt: string;
  endedAtProvisional: string;
  endedAt?: string | null;
  finalized: boolean;
  transcriptCount: number;
  summaryText?: string | null;
  cleanedText?: string | null;
  summaryProviderName?: string | null;
  hasClip?: boolean;
  // When the dashboard already has the full transcript list (e.g. from
  // /api/conversations/{id}), pass it in. Otherwise the card lazy-loads
  // when the user expands it.
  transcripts?: ConversationTranscript[];
}

const Mic = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
);

const Sparkle = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    <circle cx="12" cy="12" r="2" />
  </svg>
);

const ChevronDown = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

/**
 * Timeline card for an audio conversation. Collapses N transcript
 * rows into a single rolling artifact. When finalized, shows the
 * VLM summary on top with the raw transcript hidden behind an
 * expand toggle. While still rolling, the latest line is shown
 * with a "live" pulse.
 */
export function ConversationCard(props: ConversationCardProps) {
  const {
    id,
    cameraId,
    cameraName,
    startedAt,
    endedAtProvisional,
    endedAt,
    finalized,
    transcriptCount,
    summaryText,
    cleanedText,
    summaryProviderName,
    hasClip,
    transcripts: transcriptsProp,
  } = props;

  const { token } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [transcripts, setTranscripts] = useState<ConversationTranscript[]>(
    transcriptsProp || []
  );
  const [loadingTx, setLoadingTx] = useState(false);
  const [view, setView] = useState<"cleaned" | "raw">(
    cleanedText ? "cleaned" : "raw"
  );
  // Live-append. Accumulate transcripts pushed via WS so the user sees
  // new lines without a refetch. Latest line drives the headline while
  // the conversation is still rolling.
  const [livePulse, setLivePulse] = useState<string | null>(null);
  const [liveTranscriptCount, setLiveTranscriptCount] = useState(transcriptCount);
  useEffect(() => {
    setLiveTranscriptCount(transcriptCount);
  }, [transcriptCount]);

  useWSSubscribe(
    "conversation_updated",
    (msg) => {
      const cid = (msg as { conversation_id?: string }).conversation_id;
      if (cid !== id) return;
      const text = ((msg as { text?: string }).text || "").trim();
      if (!text) return;
      const txId = (msg as { transcript_id?: string | null }).transcript_id;
      const speaker = (msg as { speaker_name?: string | null }).speaker_name ?? null;
      const startedAtIso = (msg as { started_at?: string }).started_at || new Date().toISOString();
      const endedAtIso = (msg as { ended_at?: string }).ended_at || startedAtIso;
      setLivePulse(text);
      setLiveTranscriptCount((n) => n + 1);
      // Keep the inline transcript list in sync if the user has it
      // expanded so the new line shows up without a refetch.
      if (txId) {
        setTranscripts((prev) =>
          prev.some((t) => t.id === txId)
            ? prev
            : [
                ...prev,
                {
                  id: txId,
                  started_at: startedAtIso,
                  ended_at: endedAtIso,
                  text,
                  language: null,
                  provider: null,
                  audio_capture_id: null,
                  speaker_person_id: null,
                  speaker_source: speaker ? "video" : null,
                },
              ]
        );
      }
    },
    cameraId
  );

  useWSSubscribe(
    "conversation_finalized",
    (msg) => {
      const cid = (msg as { conversation_id?: string }).conversation_id;
      if (cid !== id) return;
      // Clear the rolling pulse; finalized state will arrive via the
      // next /api/conversations refetch the dashboard already triggers.
      setLivePulse(null);
    },
    cameraId
  );

  useEffect(() => {
    if (transcriptsProp) setTranscripts(transcriptsProp);
  }, [transcriptsProp]);

  const start = new Date(startedAt);
  const end = new Date(endedAt || endedAtProvisional);
  const durationS = Math.max(1, Math.round((end.getTime() - start.getTime()) / 1000));
  const durationLabel =
    durationS < 60
      ? `${durationS}s`
      : durationS < 3600
        ? `${Math.round(durationS / 60)}m`
        : `${(durationS / 3600).toFixed(1)}h`;

  async function loadTranscripts() {
    if (transcripts.length > 0 || loadingTx || !token) return;
    setLoadingTx(true);
    try {
      const res = await fetch(`/api/conversations/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setTranscripts(data.transcripts || []);
      }
    } finally {
      setLoadingTx(false);
    }
  }

  function handleToggle() {
    setExpanded((v) => {
      const next = !v;
      if (next) loadTranscripts();
      return next;
    });
  }

  // While rolling, prefer the most recent live pulse, then the last
  // loaded transcript, then a count placeholder. After finalize,
  // prefer the summary.
  const headlineText = finalized && summaryText
    ? summaryText
    : livePulse
      ? livePulse
      : transcripts.length > 0
        ? transcripts[transcripts.length - 1].text
        : `${liveTranscriptCount} message${liveTranscriptCount === 1 ? "" : "s"}`;

  return (
    <div
      className={`rounded-lg border overflow-hidden transition ${
        finalized
          ? "border-emerald-700/40 bg-emerald-950/20 hover:border-emerald-600/60"
          : "border-amber-700/40 bg-amber-950/15 hover:border-amber-600/60"
      }`}
    >
      <button
        type="button"
        onClick={handleToggle}
        className="w-full text-left px-3 py-2.5"
      >
        <div className="flex items-center gap-2 text-[11px] mb-1.5">
          {finalized && summaryText ? (
            <Sparkle className="w-3.5 h-3.5 text-emerald-400" />
          ) : (
            <Mic className={`w-3.5 h-3.5 ${finalized ? "text-emerald-400" : "text-amber-400"}`} />
          )}
          <span
            className={`font-medium uppercase tracking-wider ${
              finalized ? "text-emerald-300" : "text-amber-300"
            }`}
          >
            {finalized ? (summaryText ? "Conversation recap" : "Conversation") : "Conversation · live"}
          </span>
          {!finalized && (
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
            </span>
          )}
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">{cameraName || "Camera"}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground font-mono">
            {start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            {" · "}
            {durationLabel}
          </span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">
            {liveTranscriptCount} msg
          </span>
          <ChevronDown
            className={`ml-auto w-3.5 h-3.5 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        </div>
        <p
          className={`text-sm leading-relaxed ${
            finalized && summaryText ? "text-foreground" : "text-zinc-100 italic"
          }`}
        >
          {headlineText}
        </p>
        {summaryProviderName && finalized && summaryText && (
          <p className="mt-1 text-[10px] text-muted-foreground/70">
            recap by {summaryProviderName}
          </p>
        )}
      </button>

      {expanded && (
        <div className="border-t border-border/50 bg-black/30">
          <div className="px-3 pt-2 flex items-center gap-1 text-[10px] uppercase tracking-wider">
            {cleanedText && (
              <>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setView("cleaned");
                  }}
                  className={`px-2 py-1 rounded ${
                    view === "cleaned"
                      ? "bg-emerald-600/20 text-emerald-300 border border-emerald-700/40"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Cleaned
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setView("raw");
                  }}
                  className={`px-2 py-1 rounded ${
                    view === "raw"
                      ? "bg-emerald-600/20 text-emerald-300 border border-emerald-700/40"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Raw transcript
                </button>
              </>
            )}
            {finalized && (
              <div className="ml-auto">
                <ReinterpretButton
                  endpoint={`/api/conversations/${id}/reinterpret`}
                  label="Reinterpret"
                  variant="compact"
                />
              </div>
            )}
          </div>
          <div className="px-3 py-2.5 space-y-2">
            {hasClip && finalized && token && (
              <video
                controls
                preload="metadata"
                className="w-full rounded border border-border bg-black"
                src={`/api/conversations/${id}/clip?token=${encodeURIComponent(token)}`}
              />
            )}
            {view === "cleaned" && cleanedText ? (
              <p className="text-xs leading-relaxed text-zinc-200 whitespace-pre-line">
                {cleanedText}
              </p>
            ) : loadingTx && transcripts.length === 0 ? (
              <p className="text-xs text-muted-foreground">Loading transcript.</p>
            ) : transcripts.length === 0 ? (
              <p className="text-xs text-muted-foreground">No transcript rows.</p>
            ) : (
              transcripts.map((t) => (
                <TranscriptLine key={t.id} t={t} token={token} />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ResummarizeButton({
  conversationId,
  token,
}: {
  conversationId: string;
  token: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      disabled={busy || !token}
      onClick={async (e) => {
        e.stopPropagation();
        if (!token) return;
        setBusy(true);
        setDone(false);
        try {
          const res = await fetch(
            `/api/conversations/${conversationId}/resummarize`,
            {
              method: "POST",
              headers: { Authorization: `Bearer ${token}` },
            }
          );
          if (res.ok) setDone(true);
        } finally {
          setBusy(false);
          setTimeout(() => setDone(false), 2000);
        }
      }}
      className="ml-auto px-2 py-1 rounded text-muted-foreground hover:text-violet-300 hover:bg-violet-500/10 disabled:opacity-50"
    >
      {busy ? "Re-running." : done ? "Done" : "Re-summarize"}
    </button>
  );
}

function TranscriptLine({
  t,
  token,
}: {
  t: ConversationTranscript;
  token: string | null;
}) {
  const [showAudio, setShowAudio] = useState(false);
  const ts = new Date(t.started_at).toLocaleTimeString();
  const audioUrl = t.audio_capture_id && token
    ? `/api/audio/${t.audio_capture_id}?token=${encodeURIComponent(token)}`
    : null;
  return (
    <div className="text-xs">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] text-muted-foreground flex-shrink-0">
          {ts}
        </span>
        <span className="italic text-zinc-200">{t.text}</span>
        {audioUrl && !showAudio && (
          <button
            type="button"
            onClick={() => setShowAudio(true)}
            className="ml-auto text-[10px] text-emerald-400 hover:text-emerald-300 flex-shrink-0"
          >
            ▶ play
          </button>
        )}
      </div>
      {audioUrl && showAudio && (
        <audio
          controls
          autoPlay
          src={audioUrl}
          className="mt-1 h-7 w-full"
          preload="none"
        />
      )}
    </div>
  );
}
