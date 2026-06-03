"use client";

import { useState, useEffect, useRef } from "react";
import { useAuth } from "@/lib/auth";
import { useWebcamPublisher, listVideoDevices } from "@/lib/webcam-publisher";
import { STREAM_TYPES } from "@/lib/camera-types";
import type { StreamType, DiscoveredDevice, DiscoveredOnvifDevice, ModalTab } from "@/lib/camera-types";
import CameraBrandHelp from "@/components/CameraBrandHelp";

function NetworkScanPanel({ onSelectDevice }: { onSelectDevice: (dev: DiscoveredOnvifDevice, username?: string, password?: string) => void }) {
  const { authFetch } = useAuth();
  const [devices, setDevices] = useState<DiscoveredOnvifDevice[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [hasScanned, setHasScanned] = useState(false);
  const [authInputs, setAuthInputs] = useState<Record<string, { username: string; password: string }>>({});
  const [addingIp, setAddingIp] = useState<string | null>(null);

  async function handleScan() {
    setScanning(true);
    setScanError(null);
    setDevices([]);
    setHasScanned(false);
    try {
      const res = await authFetch("/api/cameras/discover?timeout=5");
      if (!res.ok) throw new Error("Network scan failed");
      const data: DiscoveredOnvifDevice[] = await res.json();
      setDevices(data);
      setHasScanned(true);
      if (data.length === 0) {
        setScanError("No ONVIF cameras found on the local network. Make sure the cameras are powered on and connected to the same network. Check that multicast traffic is not blocked by your firewall.");
      }
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed");
      setHasScanned(true);
    } finally {
      setScanning(false);
    }
  }

  function handleAuthChange(ip: string, field: "username" | "password", value: string) {
    setAuthInputs((prev) => ({ ...prev, [ip]: { ...prev[ip], [field]: value } }));
  }

  function handleAddDevice(dev: DiscoveredOnvifDevice) {
    setAddingIp(dev.ip);
    const auth = authInputs[dev.ip];
    onSelectDevice(dev, auth?.username, auth?.password);
  }

  const manufacturerIcon = (manufacturer: string) => {
    const m = manufacturer.toLowerCase();
    if (m.includes("hikvision")) return "HK";
    if (m.includes("dahua")) return "DH";
    if (m.includes("axis")) return "AX";
    if (m.includes("amcrest")) return "AM";
    if (m.includes("reolink")) return "RL";
    if (m.includes("uniview") || m.includes("unv")) return "UV";
    if (m.includes("vivotek")) return "VT";
    if (m.includes("hanwha") || m.includes("samsung")) return "HW";
    return manufacturer.slice(0, 2).toUpperCase();
  };

  return (
    <div className="space-y-4">
      <button type="button" onClick={handleScan} disabled={scanning}
        className="w-full px-3 py-3 text-sm rounded-md border border-dashed border-border hover:border-accent bg-muted/30 hover:bg-accent/5 transition-colors flex items-center justify-center gap-2.5 disabled:opacity-50 disabled:cursor-not-allowed">
        {scanning ? (
          <>
            <svg className="animate-spin h-4 w-4 text-accent-foreground" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-muted-foreground">Scanning network for ONVIF cameras...</span>
          </>
        ) : (
          <>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
              <path d="M2 12h20" />
            </svg>
            <span>{hasScanned ? "Rescan network" : "Scan Network"}</span>
          </>
        )}
      </button>

      {scanError && (
        <div className="rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">{scanError}</p>
        </div>
      )}

      {devices.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
              Found {devices.length} device{devices.length !== 1 ? "s" : ""}
            </span>
            <div className="flex-1 h-px bg-border" />
          </div>

          {devices.map((dev) => (
            <div key={dev.ip} className={`rounded-md border transition-colors ${dev.already_added ? "border-border bg-muted/10 opacity-60" : "border-border bg-muted/20 hover:border-muted-foreground"}`}>
              <div className="px-3 py-2.5 flex items-start gap-3">
                <div className="w-10 h-10 rounded-md bg-muted/50 border border-border flex items-center justify-center shrink-0">
                  <span className="text-[11px] font-bold text-muted-foreground">{manufacturerIcon(dev.manufacturer)}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{dev.name}</span>
                    {dev.already_added && (
                      <span className="shrink-0 text-[10px] font-medium text-muted-foreground bg-muted/50 px-1.5 py-0.5 rounded">Already added</span>
                    )}
                    {dev.auth_required && !dev.already_added && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-yellow-500">
                        <title>Authentication required</title>
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                      </svg>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <span className="font-mono text-[11px] text-muted-foreground">{dev.ip}</span>
                    {dev.resolution && <span className="font-mono text-[11px] text-muted-foreground px-1 py-0.5 rounded bg-muted/50">{dev.resolution}</span>}
                    {dev.profiles.length > 0 && <span className="text-[11px] text-muted-foreground">{dev.profiles.join(", ")}</span>}
                  </div>
                  {dev.firmware && <div className="text-[10px] text-muted-foreground mt-0.5 font-mono">FW {dev.firmware}</div>}
                </div>
                {!dev.already_added && (
                  <button type="button" onClick={() => handleAddDevice(dev)} disabled={addingIp === dev.ip}
                    className="shrink-0 px-2.5 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90 transition-opacity disabled:opacity-50">
                    {addingIp === dev.ip ? "Adding..." : "Add"}
                  </button>
                )}
              </div>
              {dev.auth_required && !dev.already_added && (
                <div className="px-3 pb-2.5 pt-0">
                  <div className="grid grid-cols-2 gap-2">
                    <input type="text" placeholder="Username" value={authInputs[dev.ip]?.username || ""}
                      onChange={(e) => handleAuthChange(dev.ip, "username", e.target.value)}
                      className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent" />
                    <input type="password" placeholder="Password" value={authInputs[dev.ip]?.password || ""}
                      onChange={(e) => handleAuthChange(dev.ip, "password", e.target.value)}
                      className="w-full px-2 py-1.5 text-xs rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent" />
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AddCameraModal({ onClose, onSuccess, initialStreamType }: { onClose: () => void; onSuccess: () => void; initialStreamType?: StreamType }) {
  const { authFetch } = useAuth();
  const { startPublish, stopPublish } = useWebcamPublisher();
  const [activeTab, setActiveTab] = useState<ModalTab>("manual");
  const [name, setName] = useState("");
  const [streamType, setStreamType] = useState<StreamType>(initialStreamType || "rtsp");
  const [streamUrl, setStreamUrl] = useState("");

  // Webcam state
  const [webcamDevices, setWebcamDevices] = useState<MediaDeviceInfo[]>([]);
  const [webcamDeviceId, setWebcamDeviceId] = useState<string>("");
  const [webcamStream, setWebcamStream] = useState<MediaStream | null>(null);
  const [webcamError, setWebcamError] = useState<string | null>(null);
  const webcamPreviewRef = useRef<HTMLVideoElement | null>(null);
  const [locationLabel, setLocationLabel] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [snapshotInterval, setSnapshotInterval] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [devices, setDevices] = useState<DiscoveredDevice[]>([]);
  const [scanningDevices, setScanningDevices] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [manualInput, setManualInput] = useState(false);
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState<number | null>(null);

  const selectedType = STREAM_TYPES.find((t) => t.value === streamType)!;
  const supportsAuth = ["rtsp", "http_mjpeg", "http_snapshot", "hls"].includes(streamType);
  const supportsSnapshotInterval = streamType === "http_snapshot";

  // Auto-scan when the modal opens directly into USB mode ("Add Webcam").
  useEffect(() => {
    if (initialStreamType === "usb") {
      handleDetectDevices();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDetectDevices() {
    setScanningDevices(true);
    setScanError(null);
    setDevices([]);
    setSelectedDeviceIndex(null);
    try {
      const res = await authFetch("/api/cameras/devices");
      if (!res.ok) throw new Error("Failed to scan for devices");
      const data: DiscoveredDevice[] = await res.json();
      setDevices(data);
      if (data.length === 0) setScanError("No video devices found. Try manual input instead.");
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanningDevices(false);
    }
  }

  function handleSelectDevice(device: DiscoveredDevice) {
    setSelectedDeviceIndex(device.index);
    setStreamUrl(String(device.index));
    if (!name.trim()) setName(device.name);
  }

  async function handleSubmitCamera(payload: Record<string, unknown>) {
    setSubmitting(true);
    setError(null);
    try {
      const res = await authFetch(`/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Request failed with status ${res.status}`);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add camera");
    } finally {
      setSubmitting(false);
    }
  }

  // Load devices when webcam mode enters
  useEffect(() => {
    if (streamType !== "webcam") return;
    let cancelled = false;
    (async () => {
      try {
        const list = await listVideoDevices();
        if (cancelled) return;
        setWebcamDevices(list);
        if (list.length && !webcamDeviceId) setWebcamDeviceId(list[0].deviceId);
      } catch (err) {
        setWebcamError(err instanceof Error ? err.message : "Unable to list cameras");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamType]);

  // Start preview when device selection changes (in webcam mode)
  useEffect(() => {
    if (streamType !== "webcam" || !webcamDeviceId) return;
    let active = true;
    let newStream: MediaStream | null = null;
    setWebcamError(null);
    (async () => {
      try {
        newStream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: webcamDeviceId } },
          audio: false,
        });
        if (!active) { newStream.getTracks().forEach((t) => t.stop()); return; }
        setWebcamStream((prev) => {
          prev?.getTracks().forEach((t) => t.stop());
          return newStream;
        });
        // Auto-fill Name from the selected device label if the user hasn't typed one
        const dev = webcamDevices.find((d) => d.deviceId === webcamDeviceId);
        if (dev?.label && !name.trim()) {
          const clean = dev.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim();
          if (clean) setName(clean);
        }
      } catch (err) {
        setWebcamError(err instanceof Error ? err.message : "Camera access denied");
      }
    })();
    return () => {
      active = false;
      // Don't stop the current stream here; replacement handled above
    };
  }, [streamType, webcamDeviceId]);

  // Attach stream to preview <video>
  useEffect(() => {
    if (webcamPreviewRef.current && webcamStream) {
      webcamPreviewRef.current.srcObject = webcamStream;
    }
  }, [webcamStream]);

  // Cleanup preview on close or type switch away
  useEffect(() => {
    return () => {
      webcamStream?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleWebcamSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !webcamStream) return;
    setSubmitting(true);
    setError(null);
    try {
      // Create camera row first so we get its id to key the publisher against.
      const payload: Record<string, unknown> = {
        name: name.trim(),
        stream_url: "",
        stream_type: "webcam",
        location_label: locationLabel.trim() || null,
      };
      const res = await authFetch(`/api/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Request failed with status ${res.status}`);
      }
      const created = await res.json().catch(() => null);
      if (!created?.id) throw new Error("Camera created without id");
      try {
        await startPublish({
          cameraId: created.id,
          cameraName: name.trim(),
          deviceId: webcamDeviceId,
          stream: webcamStream,
        });
        // Hand ownership of the stream to the publisher so modal cleanup won't stop it
        setWebcamStream(null);
      } catch (err) {
        stopPublish(created.id);
        throw err;
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start webcam");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleManualSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (streamType === "webcam") return handleWebcamSubmit(e);
    if (streamType === "browser_mic") {
      // Phone-as-mic. No URL needed; backend derives the tcp:// path
      // from the camera id. Audio_only flag skips the video pipeline.
      if (!name.trim()) return;
      await handleSubmitCamera({
        name: name.trim(),
        stream_url: "", // not used for browser_mic; manager derives it
        stream_type: "browser_mic",
        location_label: locationLabel.trim() || null,
        audio_only: true,
        audio_capture_enabled: true,
        audio_transcribe_enabled: true,
      });
      return;
    }
    if (streamType === "audio_rtsp") {
      if (!name.trim() || !streamUrl.trim()) return;
      const ap: Record<string, unknown> = {
        name: name.trim(),
        stream_url: streamUrl.trim(),
        // Reuse rtsp transport. PyAV av.open() handles RTSP/HTTP/file
        // uniformly so anything ffmpeg can read works.
        stream_type: "rtsp",
        location_label: locationLabel.trim() || null,
        audio_only: true,
        audio_capture_enabled: true,
        audio_transcribe_enabled: true,
      };
      if (supportsAuth && username.trim()) {
        ap.username = username.trim();
        if (password) ap.password = password;
      }
      if (supportsAuth && authToken.trim()) ap.auth_token = authToken.trim();
      await handleSubmitCamera(ap);
      return;
    }
    if (!name.trim() || !streamUrl.trim()) return;
    const payload: Record<string, unknown> = {
      name: name.trim(),
      stream_url: streamUrl.trim(),
      stream_type: streamType,
      location_label: locationLabel.trim() || null,
    };
    if (supportsAuth && username.trim()) {
      payload.username = username.trim();
      if (password) payload.password = password;
    }
    if (supportsAuth && authToken.trim()) payload.auth_token = authToken.trim();
    if (supportsSnapshotInterval) payload.snapshot_interval = snapshotInterval;
    if (streamType === "usb") payload.webcam_device = streamUrl.trim();
    await handleSubmitCamera(payload);
  }

  function handleOnvifDeviceSelect(dev: DiscoveredOnvifDevice, devUsername?: string, devPassword?: string) {
    const payload: Record<string, unknown> = {
      name: dev.name || `${dev.manufacturer} ${dev.model}`,
      stream_url: dev.stream_url || `rtsp://${dev.ip}:554/stream1`,
      stream_type: "rtsp",
    };
    if (devUsername?.trim()) payload.username = devUsername.trim();
    if (devPassword?.trim()) payload.password = devPassword.trim();
    handleSubmitCamera(payload);
  }

  const inputClass = "w-full px-3 py-2 text-sm rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg mx-4 rounded-lg border border-border bg-card-elevated p-6 shadow-xl max-h-[90vh] overflow-y-auto scrollbar-thin">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">Add Camera</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground transition-colors text-xl leading-none">&times;</button>
        </div>

        {/* Tab switcher */}
        <div className="flex gap-1 mb-5 p-1 rounded-md bg-muted/30 border border-border">
          <button type="button" onClick={() => setActiveTab("manual")}
            className={`flex-1 px-3 py-1.5 text-sm rounded transition-colors ${activeTab === "manual" ? "bg-card-elevated text-foreground font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            Manual Setup
          </button>
          <button type="button" onClick={() => setActiveTab("scan")}
            className={`flex-1 px-3 py-1.5 text-sm rounded transition-colors flex items-center justify-center gap-1.5 ${activeTab === "scan" ? "bg-card-elevated text-foreground font-medium shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /><path d="M2 12h20" />
            </svg>
            Scan Network
          </button>
        </div>

        {/* Scan Network tab */}
        {activeTab === "scan" && (
          <div>
            <NetworkScanPanel onSelectDevice={handleOnvifDeviceSelect} />
            {error && <p className="text-sm text-danger mt-3">{error}</p>}
          </div>
        )}

        {/* Manual Setup tab */}
        {activeTab === "manual" && (
          <form onSubmit={handleManualSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Name</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Front Door" required className={inputClass} />
            </div>

            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Feed Type</label>
              <div className="grid grid-cols-3 gap-1.5">
                {STREAM_TYPES.map((t) => (
                  <button key={t.value} type="button" onClick={() => { setStreamType(t.value); setStreamUrl(""); setDevices([]); setScanError(null); setSelectedDeviceIndex(null); setManualInput(false); }}
                    className={`px-2 py-2 text-xs rounded-md border transition-colors text-center ${streamType === t.value ? "border-accent bg-accent/10 text-accent-foreground" : "border-border hover:border-muted-foreground text-muted-foreground"}`}>
                    <div className="font-medium">{t.label}</div>
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-muted-foreground mt-1.5">{selectedType.hint}</p>
            </div>

            {streamType === "webcam" ? (
              <div>
                <label className="block text-sm text-muted-foreground mb-1.5">Camera Device</label>
                {webcamDevices.length > 0 ? (
                  <select value={webcamDeviceId} onChange={(e) => setWebcamDeviceId(e.target.value)} className={inputClass}>
                    {webcamDevices.map((d, i) => (
                      <option key={d.deviceId} value={d.deviceId}>{d.label || `Camera ${i + 1}`}</option>
                    ))}
                  </select>
                ) : (
                  <p className="text-[11px] text-muted-foreground">Requesting camera access...</p>
                )}
                {webcamError && <p className="text-[11px] text-danger mt-1">{webcamError}</p>}
                <div className="mt-3 rounded-md overflow-hidden border border-border bg-black aspect-video">
                  {webcamStream ? (
                    <video ref={webcamPreviewRef} autoPlay muted playsInline className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[11px] text-muted-foreground">No preview</div>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1.5">Stream stays live while this tab is open. Closing the tab stops it.</p>
              </div>
            ) : streamType === "browser_mic" ? (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
                <p className="font-medium text-foreground mb-1">No URL needed.</p>
                After you save, open the camera and tap the &quot;Open mic page&quot; button.
                On your phone, hit Start mic to publish audio over WebSocket.
                The phone stays live while the tab is open.
              </div>
            ) : (
            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">
                {streamType === "usb" ? "Device Index or Path" : streamType === "file" ? "File Path" : streamType === "audio_rtsp" ? "Audio Stream URL" : "Stream URL"}
              </label>
              {["rtsp", "http_mjpeg", "http_snapshot", "hls"].includes(streamType) && (
                <div className="mb-2">
                  <CameraBrandHelp
                    onUseTemplate={(url) => {
                      setStreamType("rtsp");
                      setStreamUrl(url);
                    }}
                  />
                </div>
              )}
              {streamType === "usb" && !manualInput ? (
                <div className="space-y-3">
                  <button type="button" onClick={handleDetectDevices} disabled={scanningDevices}
                    className="w-full px-3 py-2.5 text-sm rounded-md border border-dashed border-border hover:border-accent bg-muted/30 hover:bg-accent/5 transition-colors flex items-center justify-center gap-2 disabled:opacity-50">
                    {scanningDevices ? (
                      <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/></svg><span className="text-muted-foreground">Scanning...</span></>
                    ) : (
                      <span>{devices.length > 0 ? "Rescan devices" : "Detect devices"}</span>
                    )}
                  </button>
                  {scanError && <p className="text-[11px] text-danger">{scanError}</p>}
                  {devices.map((device) => (
                    <button key={device.index} type="button" onClick={() => handleSelectDevice(device)}
                      className={`w-full text-left px-3 py-2.5 rounded-md border transition-colors ${selectedDeviceIndex === device.index ? "border-accent bg-accent/10" : "border-border hover:border-muted-foreground bg-muted/20"}`}>
                      <div className="flex items-center justify-between">
                        <div><div className="text-sm font-medium">{device.name}</div><div className="text-[11px] text-muted-foreground font-mono">{device.path !== String(device.index) ? device.path : `index ${device.index}`}</div></div>
                        <span className="font-mono text-[11px] text-muted-foreground">{device.resolution}</span>
                      </div>
                    </button>
                  ))}
                  <button type="button" onClick={() => setManualInput(true)} className="text-[11px] text-muted-foreground hover:text-foreground">Manual input instead</button>
                  <input type="hidden" value={streamUrl} required />
                </div>
              ) : (
                <div>
                  <input type="text" value={streamUrl} onChange={(e) => setStreamUrl(e.target.value)} placeholder={selectedType.placeholder} required className={`${inputClass} font-mono text-xs`} />
                  {streamType === "usb" && (
                    <div className="flex items-center justify-between mt-1">
                      <p className="text-[11px] text-muted-foreground">Use 0 for first USB camera, 1 for second</p>
                      <button type="button" onClick={() => setManualInput(false)} className="text-[11px] text-muted-foreground hover:text-foreground shrink-0 ml-2">Detect devices</button>
                    </div>
                  )}
                </div>
              )}
            </div>
            )}

            {supportsSnapshotInterval && (
              <div>
                <label className="block text-sm text-muted-foreground mb-1.5">Poll Interval</label>
                <div className="flex items-center gap-3">
                  <input type="range" min={0.5} max={30} step={0.5} value={snapshotInterval} onChange={(e) => setSnapshotInterval(Number(e.target.value))} className="flex-1 accent-accent" />
                  <span className="font-mono text-xs text-muted-foreground w-12 text-right">{snapshotInterval}s</span>
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm text-muted-foreground mb-1.5">Location Label</label>
              <input type="text" value={locationLabel} onChange={(e) => setLocationLabel(e.target.value)} placeholder="Optional" className={inputClass} />
            </div>

            {supportsAuth && (
              <div>
                <button type="button" onClick={() => setShowAuth(!showAuth)} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                  <span className={`text-xs transition-transform ${showAuth ? "rotate-90" : ""}`}>▶</span>
                  Authentication <span className="text-[11px]">(optional)</span>
                </button>
                {showAuth && (
                  <div className="mt-3 space-y-3 pl-4 border-l border-border-subtle">
                    <div className="grid grid-cols-2 gap-3">
                      <div><label className="block text-[11px] text-muted-foreground mb-1">Username</label><input type="text" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" className={`${inputClass} text-xs`} /></div>
                      <div><label className="block text-[11px] text-muted-foreground mb-1">Password</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" className={`${inputClass} text-xs`} /></div>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground"><span className="flex-1 h-px bg-border" />or<span className="flex-1 h-px bg-border" /></div>
                    <div><label className="block text-[11px] text-muted-foreground mb-1">Bearer Token / API Key</label><input type="password" value={authToken} onChange={(e) => setAuthToken(e.target.value)} placeholder="Token for API-based cameras" className={`${inputClass} text-xs font-mono`} /></div>
                  </div>
                )}
              </div>
            )}

            {error && <p className="text-sm text-danger">{error}</p>}

            <div className="flex flex-col items-end gap-1.5 pt-2">
              {(!name.trim() || (streamType === "webcam" ? !webcamStream : streamType === "browser_mic" ? false : !streamUrl.trim())) && !submitting && (
                <p className="text-xs text-muted-foreground">
                  {!name.trim()
                    ? "Enter a Name above to continue."
                    : streamType === "webcam"
                      ? "Waiting for camera preview."
                      : "Stream URL required."}
                </p>
              )}
              <div className="flex gap-2">
                <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">Cancel</button>
                <button type="submit" disabled={submitting || !name.trim() || (streamType === "webcam" ? !webcamStream : streamType === "browser_mic" ? false : !streamUrl.trim())} className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
                  {submitting ? "Adding..." : streamType === "webcam" ? "Start Streaming" : "Add Camera"}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
