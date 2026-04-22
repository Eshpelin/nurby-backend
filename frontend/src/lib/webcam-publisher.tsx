"use client";

// Browser webcam publisher. Dead-simple design.
//
// For each camera with stream_type == "webcam" the user has set up, we
// hold a getUserMedia MediaStream in this React provider for the life of
// the tab. On a fixed interval we grab a JPEG snapshot off a hidden
// canvas and POST it to `/api/cameras/{id}/frame`. The backend drops the
// frame onto the same Redis motion stream the perception pipeline reads,
// so webcam cameras behave like any other source downstream.
//
// Persistence. Publish intent is stored in localStorage under
// `nurby:webcam-intent`. On provider mount we try to resume each intent
// automatically. If the browser has not granted camera permission yet,
// the intent stays on disk and the UI shows a one-click enable button.
//
// Cross-tab coordination. BroadcastChannel("nurby:webcam") prevents two
// tabs from publishing the same camera at once. Whichever tab claims
// first wins. On tab close, the owner releases so another tab can pick
// up.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

const INTENT_KEY = "nurby:webcam-intent";
const CHANNEL_NAME = "nurby:webcam";
const FRAME_INTERVAL_MS = 1000; // one JPEG per second
const FRAME_QUALITY = 0.7;
const FRAME_MAX_WIDTH = 960;

export type PublisherStatus =
  | "connecting"
  | "live"
  | "error"
  | "needs-permission"
  | "held-by-other-tab";

export interface WebcamPublisher {
  /** stable key. currently the camera id as a string. */
  key: string;
  cameraId: string;
  cameraName: string;
  deviceId: string | null;
  status: PublisherStatus;
  error?: string;
  /** Live preview stream, when this tab owns the capture. */
  stream?: MediaStream;
}

export interface WebcamIntent {
  cameraId: string;
  cameraName: string;
  deviceId: string;
}

interface WebcamContextValue {
  publishers: WebcamPublisher[];
  /** Start capturing + uploading frames for this camera. */
  startPublish: (opts: {
    cameraId: string;
    cameraName: string;
    stream: MediaStream;
    deviceId?: string | null;
  }) => Promise<void>;
  /** Stop capturing, release camera, and forget intent. */
  stopPublish: (cameraId: string) => void;
  /** Attempt to resume a saved intent (retries getUserMedia). */
  resumeIntent: (cameraId: string) => Promise<void>;
  /** Read raw intents from storage. */
  getIntents: () => WebcamIntent[];
  /** Live MediaStream for a camera, if any. */
  getStream: (cameraId: string) => MediaStream | null;
}

const WebcamContext = createContext<WebcamContextValue | null>(null);

interface ActiveSession {
  stream: MediaStream;
  video: HTMLVideoElement;
  canvas: HTMLCanvasElement;
  timer: ReturnType<typeof setInterval>;
  uploading: boolean;
}

function loadIntents(): WebcamIntent[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(INTENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x): x is WebcamIntent =>
        !!x &&
        typeof x.cameraId === "string" &&
        typeof x.cameraName === "string" &&
        typeof x.deviceId === "string"
    );
  } catch {
    return [];
  }
}

function saveIntents(list: WebcamIntent[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(INTENT_KEY, JSON.stringify(list));
  } catch {
    /* quota. ignore */
  }
}

function upsertIntent(next: WebcamIntent) {
  const list = loadIntents().filter((x) => x.cameraId !== next.cameraId);
  list.push(next);
  saveIntents(list);
}

function removeIntent(cameraId: string) {
  saveIntents(loadIntents().filter((x) => x.cameraId !== cameraId));
}

export function WebcamPublisherProvider({ children }: { children: React.ReactNode }) {
  const sessionsRef = useRef<Map<string, ActiveSession>>(new Map());
  const channelRef = useRef<BroadcastChannel | null>(null);
  const heldByOtherRef = useRef<Set<string>>(new Set());
  const inflightRef = useRef<Set<string>>(new Set());
  const didInitRef = useRef(false);
  const [publishers, setPublishers] = useState<WebcamPublisher[]>([]);

  const upsertPub = useCallback((p: WebcamPublisher) => {
    setPublishers((prev) => {
      const next = prev.filter((x) => x.cameraId !== p.cameraId);
      next.push(p);
      return next;
    });
  }, []);

  const removePub = useCallback((cameraId: string) => {
    setPublishers((prev) => prev.filter((x) => x.cameraId !== cameraId));
  }, []);

  const uploadFrame = useCallback(async (cameraId: string) => {
    const session = sessionsRef.current.get(cameraId);
    if (!session || session.uploading) return;
    const { video, canvas } = session;
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    console.debug("[webcam] tick", cameraId, vw, vh, "readyState", video.readyState);
    if (!vw || !vh) return;

    // Scale down if huge. keeps uploads snappy.
    const scale = vw > FRAME_MAX_WIDTH ? FRAME_MAX_WIDTH / vw : 1;
    const w = Math.round(vw * scale);
    const h = Math.round(vh * scale);
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, w, h);

    session.uploading = true;
    try {
      const blob: Blob | null = await new Promise((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/jpeg", FRAME_QUALITY)
      );
      if (!blob) return;
      const token = typeof window !== "undefined" ? localStorage.getItem("nurby_token") : null;
      const headers: Record<string, string> = { "Content-Type": "image/jpeg" };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`/api/cameras/${cameraId}/frame`, {
        method: "POST",
        headers,
        body: blob,
      });
      if (!res.ok) {
        console.warn("[webcam] frame upload failed", cameraId, res.status, await res.text().catch(() => ""));
      }
    } catch (err) {
      console.warn("[webcam] frame upload error", cameraId, err);
    } finally {
      session.uploading = false;
    }
  }, []);

  const startPublish = useCallback(
    async (opts: {
      cameraId: string;
      cameraName: string;
      stream: MediaStream;
      deviceId?: string | null;
    }) => {
      const { cameraId, cameraName, stream } = opts;
      const deviceId = opts.deviceId ?? null;

      if (heldByOtherRef.current.has(cameraId)) {
        upsertPub({
          key: cameraId,
          cameraId,
          cameraName,
          deviceId,
          status: "held-by-other-tab",
        });
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      // Drop duplicate starts.
      if (sessionsRef.current.has(cameraId) || inflightRef.current.has(cameraId)) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      inflightRef.current.add(cameraId);
      upsertPub({ key: cameraId, cameraId, cameraName, deviceId, status: "connecting", stream });

      // Announce ownership.
      try {
        channelRef.current?.postMessage({ type: "claim", cameraId });
      } catch {
        /* no-op */
      }

      // Build hidden video + canvas pair.
      const video = document.createElement("video");
      video.autoplay = true;
      video.playsInline = true;
      video.muted = true;
      video.srcObject = stream;
      try {
        await video.play();
      } catch {
        /* some browsers need user gesture. play resumes later. */
      }

      const canvas = document.createElement("canvas");
      const timer = setInterval(() => {
        uploadFrame(cameraId).catch(() => undefined);
      }, FRAME_INTERVAL_MS);

      sessionsRef.current.set(cameraId, { stream, video, canvas, timer, uploading: false });
      inflightRef.current.delete(cameraId);
      upsertPub({ key: cameraId, cameraId, cameraName, deviceId, status: "live", stream });

      if (deviceId) {
        upsertIntent({ cameraId, cameraName, deviceId });
      }
    },
    [uploadFrame, upsertPub]
  );

  const stopPublish = useCallback(
    (cameraId: string) => {
      const session = sessionsRef.current.get(cameraId);
      if (session) {
        clearInterval(session.timer);
        session.stream.getTracks().forEach((t) => t.stop());
        try {
          session.video.srcObject = null;
        } catch {
          /* no-op */
        }
        sessionsRef.current.delete(cameraId);
      }
      removePub(cameraId);
      removeIntent(cameraId);
      try {
        channelRef.current?.postMessage({ type: "release", cameraId });
      } catch {
        /* no-op */
      }
    },
    [removePub]
  );

  const resumeIntent = useCallback(
    async (cameraId: string) => {
      const intent = loadIntents().find((x) => x.cameraId === cameraId);
      if (!intent) return;
      if (heldByOtherRef.current.has(cameraId)) {
        upsertPub({
          key: cameraId,
          cameraId,
          cameraName: intent.cameraName,
          deviceId: intent.deviceId,
          status: "held-by-other-tab",
        });
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: intent.deviceId } },
        });
        await startPublish({
          cameraId,
          cameraName: intent.cameraName,
          deviceId: intent.deviceId,
          stream,
        });
      } catch {
        upsertPub({
          key: cameraId,
          cameraId,
          cameraName: intent.cameraName,
          deviceId: intent.deviceId,
          status: "needs-permission",
        });
      }
    },
    [startPublish, upsertPub]
  );

  const getIntents = useCallback(() => loadIntents(), []);
  const getStream = useCallback(
    (cameraId: string) => sessionsRef.current.get(cameraId)?.stream ?? null,
    []
  );

  // Init. auto-resume saved intents. cross-tab coordination. unload cleanup.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (didInitRef.current) return;
    didInitRef.current = true;

    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel(CHANNEL_NAME);
      channelRef.current = channel;
      channel.onmessage = (ev) => {
        const msg = ev.data as { type?: string; cameraId?: string } | null;
        if (!msg || !msg.cameraId) return;
        const id = msg.cameraId;
        if (msg.type === "claim") {
          heldByOtherRef.current.add(id);
          // If we already hold this camera, yield to the newer claim.
          const session = sessionsRef.current.get(id);
          if (session) {
            clearInterval(session.timer);
            session.stream.getTracks().forEach((t) => t.stop());
            sessionsRef.current.delete(id);
          }
          setPublishers((prev) =>
            prev.map((p) =>
              p.cameraId === id ? { ...p, status: "held-by-other-tab", stream: undefined } : p
            )
          );
        } else if (msg.type === "release") {
          heldByOtherRef.current.delete(id);
          resumeIntent(id).catch(() => undefined);
        }
      };
    } catch {
      /* BroadcastChannel unavailable. single-tab mode. */
    }

    // Auto-resume every saved intent.
    loadIntents().forEach((intent) => {
      resumeIntent(intent.cameraId).catch(() => undefined);
    });

    const onUnload = () => {
      sessionsRef.current.forEach((session, cameraId) => {
        clearInterval(session.timer);
        session.stream.getTracks().forEach((t) => t.stop());
        try {
          channel?.postMessage({ type: "release", cameraId });
        } catch {
          /* no-op */
        }
      });
    };
    window.addEventListener("beforeunload", onUnload);
    window.addEventListener("pagehide", onUnload);

    // No cleanup in dev. StrictMode teardown would stop the live
    // publisher right after starting it. Provider lives for the life of
    // the app.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <WebcamContext.Provider
      value={{ publishers, startPublish, stopPublish, resumeIntent, getIntents, getStream }}
    >
      {children}
    </WebcamContext.Provider>
  );
}

export function useWebcamPublisher() {
  const ctx = useContext(WebcamContext);
  if (!ctx)
    throw new Error("useWebcamPublisher must be used within WebcamPublisherProvider");
  return ctx;
}

// Enumerate video devices. Triggers a brief getUserMedia prompt if labels
// aren't yet available (browsers hide labels until permission granted).
export async function listVideoDevices(): Promise<MediaDeviceInfo[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const videos = devices.filter((d) => d.kind === "videoinput");
  if (videos.length === 0) return [];
  if (videos.every((d) => !d.label)) {
    try {
      const tmp = await navigator.mediaDevices.getUserMedia({ video: true });
      tmp.getTracks().forEach((t) => t.stop());
      const again = await navigator.mediaDevices.enumerateDevices();
      return again.filter((d) => d.kind === "videoinput");
    } catch {
      return videos;
    }
  }
  return videos;
}
