"use client";

// Persistent bar showing the state of any browser-published webcams.
// Shows one chip per publisher. live publishers get a Stop button.
// publishers in needs-permission state get an Enable button that retries
// getUserMedia. publishers held by another tab render a muted chip.

import { useWebcamPublisher } from "@/lib/webcam-publisher";

export function WebcamPublishBar() {
  const { publishers, stopPublish, resumeIntent } = useWebcamPublisher();

  if (publishers.length === 0) return null;

  return (
    <div className="fixed bottom-3 right-3 z-50 flex flex-col gap-1.5 pointer-events-none">
      {publishers.map((p) => {
        const base =
          "pointer-events-auto flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs shadow-sm backdrop-blur-sm";
        if (p.status === "live") {
          return (
            <div
              key={p.cameraId}
              className={`${base} border-accent/40 bg-accent/10 text-foreground`}
            >
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
              </span>
              <span className="truncate max-w-[180px]">{p.cameraName}</span>
              <span className="text-muted-foreground">streaming</span>
              <button
                onClick={() => stopPublish(p.cameraId)}
                className="ml-1 text-muted-foreground hover:text-foreground"
                aria-label="Stop streaming"
              >
                Stop
              </button>
            </div>
          );
        }
        if (p.status === "connecting") {
          return (
            <div
              key={p.cameraId}
              className={`${base} border-border bg-card/80 text-muted-foreground`}
            >
              <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
              <span className="truncate max-w-[180px]">{p.cameraName}</span>
              <span>connecting.</span>
            </div>
          );
        }
        if (p.status === "needs-permission") {
          return (
            <div
              key={p.cameraId}
              className={`${base} border-amber-500/40 bg-amber-500/10 text-foreground`}
            >
              <span className="h-2 w-2 rounded-full bg-amber-400" />
              <span className="truncate max-w-[180px]">{p.cameraName}</span>
              <span className="text-muted-foreground">needs camera access</span>
              <button
                onClick={() => resumeIntent(p.cameraId)}
                className="ml-1 rounded bg-amber-500/80 text-black px-2 py-0.5 font-medium hover:bg-amber-500"
              >
                Enable
              </button>
            </div>
          );
        }
        if (p.status === "held-by-other-tab") {
          return (
            <div
              key={p.cameraId}
              className={`${base} border-border bg-card/60 text-muted-foreground`}
            >
              <span className="h-2 w-2 rounded-full bg-muted-foreground/60" />
              <span className="truncate max-w-[180px]">{p.cameraName}</span>
              <span>streaming in another tab</span>
            </div>
          );
        }
        // error
        return (
          <div
            key={p.cameraId}
            className={`${base} border-red-500/40 bg-red-500/10 text-foreground`}
          >
            <span className="h-2 w-2 rounded-full bg-red-500" />
            <span className="truncate max-w-[180px]">{p.cameraName}</span>
            <span className="text-muted-foreground truncate max-w-[200px]">
              {p.error || "error"}
            </span>
            <button
              onClick={() => resumeIntent(p.cameraId)}
              className="ml-1 text-muted-foreground hover:text-foreground"
            >
              Retry
            </button>
            <button
              onClick={() => stopPublish(p.cameraId)}
              className="ml-1 text-muted-foreground hover:text-foreground"
            >
              Stop
            </button>
          </div>
        );
      })}
    </div>
  );
}
