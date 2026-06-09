"use client";

import { useEffect, useRef, useState } from "react";
import { useWSSubscribe } from "@/lib/ws";

interface Person {
  track_id: number;
  person_id?: string | null;
  person_name?: string | null;
  action: string;
  confidence?: number;
}

interface Props {
  cameraId: string;
  position?: "top" | "bottom";
  // Clear the strip if no update arrives within this window.
  staleMs?: number;
}

const HIDDEN_ACTIONS = new Set(["unknown"]);

function label(p: Person): string {
  const who = p.person_name || (p.person_id ? "Person" : "Someone");
  return `${who} · ${p.action}`;
}

/**
 * Live current-activity strip for a camera tile (HAR). Subscribes to the
 * `person_actions` broadcast and shows a compact line of who is doing what
 * right now. `fallen` is pinned red. Mirrors LiveCaptionOverlay's pattern
 * (translucent strip, camera-filtered WS) rather than per-person boxes, since
 * the live tile has no detection-coordinate overlay. Renders nothing until an
 * update arrives, so it is invisible when HAR is off.
 */
export function CurrentActivityStrip({ cameraId, position = "top", staleMs = 8000 }: Props) {
  const [people, setPeople] = useState<Person[]>([]);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useWSSubscribe(
    "person_actions",
    (msg) => {
      const raw = (msg as { people?: Person[] }).people || [];
      const shown = raw.filter((p) => p && !HIDDEN_ACTIONS.has(p.action));
      setPeople(shown);
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => setPeople([]), staleMs);
    },
    cameraId
  );

  useEffect(
    () => () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    },
    []
  );

  if (people.length === 0) return null;

  const posClass = position === "top" ? "top-2 left-2 right-2" : "bottom-10 left-2 right-2";
  const hasFall = people.some((p) => p.action === "fallen");

  return (
    <div
      role="status"
      aria-live="polite"
      className={`absolute ${posClass} z-20 pointer-events-none flex flex-wrap items-center gap-1.5 rounded-md px-2 py-1 backdrop-blur-sm border ${
        hasFall ? "bg-red-950/70 border-red-500/40" : "bg-black/65 border-white/10"
      }`}
    >
      {people.slice(0, 4).map((p) => (
        <span
          key={p.track_id}
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
            p.action === "fallen"
              ? "bg-red-500/30 text-red-200"
              : "bg-white/10 text-white/90"
          }`}
        >
          {label(p)}
        </span>
      ))}
    </div>
  );
}
