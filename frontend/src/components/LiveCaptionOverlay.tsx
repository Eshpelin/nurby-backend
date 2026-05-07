"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useWSSubscribe } from "@/lib/ws";

interface Props {
  cameraId: string;
  position?: "top" | "bottom";
  // Hold-time per caption before auto-hide.
  holdMs?: number;
}

interface Caption {
  text: string;
  speaker?: string | null;
  ts: number;
}

/**
 * Renders the most recent transcript_created payload for this camera
 * as a translucent caption strip on top of the camera tile. The
 * shown text reveals one character at a time (typewriter) so chunked
 * captions feel live without the cost of true streaming STT.
 *
 * REST hydrate on mount so a page reload inside an active speech
 * window shows the last caption immediately. Hold time stretches
 * with caption length so long sentences finish on screen. Speaker
 * name (when known via Tier A attribution) prefixes the text in
 * emerald.
 */
export function LiveCaptionOverlay({
  cameraId,
  position = "bottom",
  holdMs = 6000,
}: Props) {
  const { token } = useAuth();
  const [caption, setCaption] = useState<Caption | null>(null);
  const [reveal, setReveal] = useState<string>("");
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const typeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setLine = useCallback(
    (text: string, speaker?: string | null) => {
      const ts = Date.now();
      setCaption({ text, speaker: speaker ?? null, ts });
      setReveal("");
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => setCaption(null), computeHold(text, holdMs));
    },
    [holdMs]
  );

  // Typewriter reveal. Step ~24 chars/sec; clamp total reveal to 1.5s
  // for short lines and 5s for long ones. The full text is already in
  // ``caption.text`` so the hold timer can keep the finished line
  // on screen after the typewriter catches up.
  useEffect(() => {
    if (typeTimer.current) clearTimeout(typeTimer.current);
    if (!caption) return;
    const total = caption.text.length;
    if (total === 0) return;
    const totalMs = Math.max(800, Math.min(5000, total * 32));
    const step = Math.max(8, totalMs / total);
    let i = 0;
    const tick = () => {
      i = Math.min(total, i + Math.max(1, Math.round(step >= 24 ? 1 : 24 / step)));
      setReveal(caption.text.slice(0, i));
      if (i < total) typeTimer.current = setTimeout(tick, step);
    };
    tick();
    return () => {
      if (typeTimer.current) clearTimeout(typeTimer.current);
    };
  }, [caption]);

  // REST hydrate. Pull the most recent transcript so a fresh mount
  // (e.g. after reload) shows the caption that was on screen before.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(
          `/api/transcripts?camera_id=${cameraId}&limit=1`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!resp.ok) return;
        const rows = await resp.json();
        if (cancelled) return;
        const last = Array.isArray(rows) ? rows[0] : null;
        if (!last) return;
        const text = (last.text || "").trim();
        if (!text) return;
        const ageMs = Date.now() - new Date(last.started_at).getTime();
        if (ageMs > holdMs) return;
        setLine(text);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cameraId, token, holdMs, setLine]);

  useWSSubscribe(
    "transcript_created",
    (msg) => {
      const text = ((msg as { text?: string }).text || "").trim();
      if (!text) return;
      const speaker = (msg as { speaker_name?: string | null }).speaker_name;
      setLine(text, speaker);
    },
    cameraId
  );

  useEffect(
    () => () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
      if (typeTimer.current) clearTimeout(typeTimer.current);
    },
    []
  );

  if (!caption) return null;

  const posClass =
    position === "top"
      ? "top-2 left-2 right-2"
      : "bottom-10 left-2 right-2";

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={`absolute ${posClass} z-20 pointer-events-none flex items-start gap-2 rounded-md bg-black/70 px-2.5 py-1.5 backdrop-blur-sm border border-white/10 animate-[fadeIn_0.25s_ease-out]`}
    >
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="mt-0.5 flex-shrink-0 text-emerald-400"
        aria-hidden="true"
      >
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      </svg>
      <span className="text-[11px] leading-snug text-white/90 line-clamp-2 italic">
        {caption.speaker && (
          <span className="not-italic font-medium text-emerald-300 mr-1">
            {caption.speaker}.
          </span>
        )}
        {reveal || caption.text}
      </span>
    </div>
  );
}

function computeHold(text: string, floor: number): number {
  const stretched = floor + Math.min(8000, text.length * 60);
  return Math.min(14000, Math.max(floor, stretched));
}
