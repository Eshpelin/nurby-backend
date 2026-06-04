"use client";

import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

interface RecordingLike {
  id: string;
  camera_id: string;
  started_at: string;
  ended_at?: string | null;
  duration_seconds?: number | null;
  file_size_bytes?: number | null;
}

interface Props {
  recording: RecordingLike | null;
  cameraName?: string | null;
  onClose: () => void;
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function fmtDuration(s: number | null | undefined): string {
  if (!s) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h) return `${h}h ${m}m ${sec}s`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function fmtSize(b: number | null | undefined): string {
  if (!b) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function RecordingModal({ recording, cameraName, onClose }: Props) {
  const { token } = useAuth();
  const tq = token ? `?token=${token}` : "";
  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [recording, onClose]);

  if (!recording) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl rounded-lg border border-border bg-card shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-4 py-3 border-b border-border">
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">
              {cameraName || "Recording"}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">
              {fmtDateTime(recording.started_at)}
              {recording.duration_seconds != null && (
                <>
                  <span className="mx-2">&middot;</span>
                  {fmtDuration(recording.duration_seconds)}
                </>
              )}
              {recording.file_size_bytes != null && (
                <>
                  <span className="mx-2">&middot;</span>
                  {fmtSize(recording.file_size_bytes)}
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="p-4 space-y-3">
          <video
            key={recording.id}
            controls
            autoPlay
            className="w-full max-h-[60vh] rounded bg-black"
            src={`/api/recordings/${recording.id}/stream${tq}`}
          />
          <div className="flex items-center justify-end">
            <a
              href={`/api/recordings/${recording.id}/download${tq}`}
              download
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Download
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
