"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

/**
 * Phone-as-mic publisher page. Visited by the phone in a browser
 * tab. Captures audio with MediaRecorder (webm/opus) and posts
 * binary chunks to /ws/mic/<camera_id>. Backend pipes them through
 * ffmpeg into a deterministic TCP port that the ingestion
 * AudioWorker consumes.
 */
export default function MicPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { token } = useAuth();
  const [status, setStatus] = useState<
    "idle" | "requesting" | "live" | "error" | "stopping"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [chunkCount, setChunkCount] = useState(0);
  const [vol, setVol] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const mrRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    setStatus("stopping");
    try {
      mrRef.current?.stop();
    } catch {
      /* ignore */
    }
    try {
      wsRef.current?.close();
    } catch {
      /* ignore */
    }
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    } catch {
      /* ignore */
    }
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    try {
      audioCtxRef.current?.close();
    } catch {
      /* ignore */
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    streamRef.current = null;
    mrRef.current = null;
    wsRef.current = null;
    setStatus("idle");
  }, []);

  const start = useCallback(async () => {
    if (!token) {
      setError("Sign in first");
      return;
    }
    setStatus("requesting");
    setError(null);
    setChunkCount(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      streamRef.current = stream;

      // Level meter. Tiny analyser so the user sees their voice land.
      const ctx = new (window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext)();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = Math.abs(buf[i] - 128);
          if (v > peak) peak = v;
        }
        setVol(peak / 128);
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;

      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${window.location.host}/ws/mic/${id}?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        // Browsers vary on supported MediaRecorder mime types. Try
        // opus webm first; fall back to plain webm.
        let mr: MediaRecorder | null = null;
        const tryTypes = [
          "audio/webm;codecs=opus",
          "audio/webm",
          "audio/ogg;codecs=opus",
        ];
        for (const t of tryTypes) {
          if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(t)) {
            mr = new MediaRecorder(stream, { mimeType: t, audioBitsPerSecond: 32000 });
            break;
          }
        }
        if (!mr) {
          setError("Browser does not support recording");
          setStatus("error");
          ws.close();
          return;
        }
        mrRef.current = mr;
        mr.ondataavailable = (e) => {
          if (e.data && e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
            e.data.arrayBuffer().then((ab) => {
              try {
                ws.send(ab);
                setChunkCount((c) => c + 1);
              } catch {
                /* ignore */
              }
            });
          }
        };
        // 250ms chunks. Short enough that the audio worker stays live;
        // long enough that overhead is negligible.
        mr.start(250);
        setStatus("live");
      };
      ws.onmessage = (evt) => {
        if (typeof evt.data !== "string") return;
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "error") {
            setError(String(msg.message || "stream error"));
          }
        } catch {
          /* ignore */
        }
      };
      ws.onerror = () => {
        setError("Connection lost");
      };
      ws.onclose = () => {
        if (status === "live" || status === "requesting") {
          setStatus("error");
        }
      };
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Microphone access denied");
      setStatus("error");
      stop();
    }
  }, [id, status, stop, token]);

  useEffect(() => {
    return () => stop();
  }, [stop]);

  // Tip: keep the screen awake when live. Best-effort.
  useEffect(() => {
    if (status !== "live") return;
    let wakeLock: { release: () => Promise<void> } | null = null;
    (async () => {
      try {
        const nav = navigator as unknown as { wakeLock?: { request: (t: string) => Promise<typeof wakeLock> } };
        if (nav.wakeLock) {
          wakeLock = await nav.wakeLock.request("screen");
        }
      } catch {
        /* ignore */
      }
    })();
    return () => {
      wakeLock?.release().catch(() => undefined);
    };
  }, [status]);

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm space-y-5">
        <div className="text-center">
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← Dashboard
          </Link>
          <h1 className="text-xl font-semibold mt-2 flex items-center gap-2 justify-center">
            <MicIcon className="w-5 h-5 text-emerald-400" />
            Phone mic
          </h1>
          <p className="text-xs text-muted-foreground mt-1 break-all">
            camera {id.slice(0, 8)}.
          </p>
        </div>

        {/* Big tap target */}
        <div className="flex flex-col items-center gap-4">
          <button
            type="button"
            onClick={status === "live" ? stop : start}
            disabled={status === "requesting" || status === "stopping"}
            className={`relative w-44 h-44 rounded-full text-base font-medium border-2 transition-colors ${
              status === "live"
                ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                : status === "error"
                  ? "border-danger bg-danger/10 text-danger"
                  : "border-border bg-card hover:border-accent"
            }`}
          >
            {status === "live" && (
              <span className="absolute inset-0 rounded-full animate-ping bg-emerald-500/25" />
            )}
            <span className="relative flex flex-col items-center justify-center h-full gap-2">
              <MicIcon className="w-10 h-10" />
              {status === "live"
                ? "Stop mic"
                : status === "requesting"
                  ? "Connecting."
                  : status === "stopping"
                    ? "Stopping."
                    : "Start mic"}
            </span>
          </button>

          {/* Level meter */}
          <div className="w-full h-2 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-emerald-400 transition-all"
              style={{ width: `${Math.min(100, Math.round(vol * 200))}%` }}
            />
          </div>

          {status === "live" && (
            <p className="text-[11px] text-muted-foreground font-mono">
              sending · {chunkCount} chunks
            </p>
          )}

          {error && (
            <p className="text-xs text-danger text-center">{error}</p>
          )}

          <div className="text-[11px] text-muted-foreground text-center leading-relaxed mt-2">
            Keep this tab open while you want the mic live. The screen
            stays awake automatically.
          </div>
        </div>
      </div>
    </div>
  );
}

function MicIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  );
}
