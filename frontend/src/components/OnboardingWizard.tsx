"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { CAMERA_PERSONAS, type PersonaPatch } from "@/lib/camera-personas";

interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  default_model: string | null;
  active: boolean;
}

interface Props {
  onClose: () => void;
  onComplete: () => void;
}

type Step = "welcome" | "provider" | "camera" | "summary" | "done";

const PROVIDER_PRESETS = [
  {
    kind: "ollama",
    name: "Local (Ollama)",
    base_url: "http://localhost:11434",
    default_model: "gemma3:4b",
    keyRequired: false,
    hint: "Runs locally. No API key. No data leaves your network.",
  },
  {
    kind: "openai",
    name: "OpenAI",
    base_url: "https://api.openai.com",
    default_model: "gpt-4o-mini",
    keyRequired: true,
    hint: "Cloud. Best image understanding. Pay-per-call.",
  },
  {
    kind: "anthropic",
    name: "Anthropic Claude",
    base_url: "https://api.anthropic.com",
    default_model: "claude-sonnet-4-20250514",
    keyRequired: true,
    hint: "Cloud. Strong at language and reasoning.",
  },
  {
    kind: "google",
    name: "Google Gemini",
    base_url: "https://generativelanguage.googleapis.com",
    default_model: "gemini-2.0-flash",
    keyRequired: true,
    hint: "Cloud. Generous free tier.",
  },
];

const STREAM_TYPES = [
  { value: "rtsp", label: "RTSP", placeholder: "rtsp://192.168.1.50:554/stream1" },
  { value: "http_mjpeg", label: "HTTP MJPEG", placeholder: "http://192.168.1.50/video" },
  { value: "http_snapshot", label: "HTTP Snapshot", placeholder: "http://192.168.1.50/snapshot.jpg" },
  { value: "hls", label: "HLS", placeholder: "https://example.com/stream.m3u8" },
];

/**
 * Multi-step modal that walks a fresh user through:
 *   1. welcome
 *   2. pick / create a VLM provider
 *   3. add their first camera with a persona preset
 *   4. confirm summary mode
 *   5. done
 *
 * Each step is skippable. Completing the wizard sets a localStorage
 * flag so it does not pop up again. The dashboard decides when to
 * mount this (see /app/page.tsx).
 */
export function OnboardingWizard({ onClose, onComplete }: Props) {
  const { authFetch } = useAuth();
  const [step, setStep] = useState<Step>("welcome");
  const [providers, setProviders] = useState<Provider[]>([]);

  // Persist dismissal both locally (fast path) and server-side (so it
  // survives a browser/device change; an admin can re-trigger the wizard
  // by flipping onboarding_dismissed back to false in Settings).
  const markDismissed = useCallback(() => {
    try {
      localStorage.setItem("nurby-onboarding-dismissed", "1");
    } catch {
      /* ignore */
    }
    authFetch("/api/system/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ onboarding_dismissed: true }),
    }).catch(() => {
      /* best-effort; localStorage still gates this browser */
    });
  }, [authFetch]);

  // Provider step state.
  const [presetIdx, setPresetIdx] = useState<number>(0);
  const [providerName, setProviderName] = useState<string>(PROVIDER_PRESETS[0].name);
  const [providerApiKey, setProviderApiKey] = useState<string>("");
  const [providerModel, setProviderModel] = useState<string>(PROVIDER_PRESETS[0].default_model);
  const [providerSubmitting, setProviderSubmitting] = useState(false);
  const [providerError, setProviderError] = useState<string | null>(null);
  // Connection-test state. After we create the provider row we hit the
  // backend test endpoint so a wrong key / unreachable endpoint fails
  // fast in the wizard instead of silently later. forceAdvance lets the
  // user proceed past a failed test on a second click.
  const [providerTestMsg, setProviderTestMsg] = useState<string | null>(null);
  const [providerForceAdvance, setProviderForceAdvance] = useState(false);
  const [createdProviderId, setCreatedProviderId] = useState<string | null>(null);
  const [skipProvider, setSkipProvider] = useState(false);

  // Camera step state.
  const [camName, setCamName] = useState("Front Door");
  const [camStreamType, setCamStreamType] = useState("rtsp");
  const [camStreamUrl, setCamStreamUrl] = useState("");
  const [camLocation, setCamLocation] = useState("");
  const [camPersonaId, setCamPersonaId] = useState<string>("front-door");
  const [camSubmitting, setCamSubmitting] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);

  const preset = PROVIDER_PRESETS[presetIdx];
  const persona = useMemo(
    () => CAMERA_PERSONAS.find((p) => p.id === camPersonaId),
    [camPersonaId]
  );

  // Auto-pick provider name + default model from preset.
  useEffect(() => {
    setProviderName(PROVIDER_PRESETS[presetIdx].name);
    setProviderModel(PROVIDER_PRESETS[presetIdx].default_model);
  }, [presetIdx]);

  // Hydrate existing providers so we can skip step 2 if one already
  // exists.
  useEffect(() => {
    (async () => {
      try {
        const r = await authFetch("/api/providers");
        if (r.ok) {
          const list: Provider[] = await r.json();
          setProviders(list);
        }
      } catch {
        /* ignore */
      }
    })();
  }, [authFetch]);

  async function createProvider(): Promise<Provider | null> {
    setProviderError(null);
    setProviderSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        name: providerName.trim() || preset.name,
        kind: preset.kind,
        base_url: preset.base_url,
        default_model: providerModel.trim() || preset.default_model,
        active: true,
      };
      if (preset.keyRequired) {
        if (!providerApiKey.trim()) {
          setProviderError("API key is required for this provider");
          return null;
        }
        body.api_key = providerApiKey.trim();
      }
      const res = await authFetch("/api/providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        setProviderError(j.detail || `Failed (${res.status})`);
        return null;
      }
      const created: Provider = await res.json();
      setProviders((prev) => [...prev, created]);
      setCreatedProviderId(created.id);
      return created;
    } finally {
      setProviderSubmitting(false);
    }
  }

  /** Hit /providers/{id}/test. Returns true on a confirmed connection. */
  async function testProvider(providerId: string): Promise<boolean> {
    setProviderTestMsg("Testing connection...");
    try {
      const res = await authFetch(`/api/providers/${providerId}/test`, {
        method: "POST",
      });
      const j = await res.json().catch(() => ({}));
      if (res.ok && j.ok) {
        const lat = j.latency_ms != null ? ` (${j.latency_ms}ms)` : "";
        setProviderTestMsg(`Connected${lat}. ${j.message || ""}`.trim());
        return true;
      }
      setProviderTestMsg(
        `Connection test failed: ${j.message || j.detail || `status ${res.status}`}. ` +
          "Check the key / URL, or click again to continue anyway.",
      );
      return false;
    } catch {
      setProviderTestMsg(
        "Could not reach the provider to test it. Click again to continue anyway.",
      );
      return false;
    }
  }

  async function createCamera(): Promise<boolean> {
    setCamError(null);
    setCamSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        name: camName.trim() || "Camera 1",
        stream_url: camStreamUrl.trim(),
        stream_type: camStreamType,
        location_label: camLocation.trim() || null,
      };
      if (persona) {
        // Spread the persona patch into the create payload. Backend
        // accepts the same field shape.
        const patch: PersonaPatch = persona.patch;
        for (const [k, v] of Object.entries(patch)) {
          if (v !== undefined) body[k] = v;
        }
      }
      const res = await authFetch("/api/cameras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        setCamError(j.detail || `Failed (${res.status})`);
        return false;
      }
      return true;
    } finally {
      setCamSubmitting(false);
    }
  }

  function dismiss() {
    markDismissed();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="rounded-xl border border-border bg-card w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-accent" />
            <h2 className="text-sm font-semibold uppercase tracking-wider">
              Set up Nurby
            </h2>
            <span className="text-xs text-muted-foreground">
              Step {stepNumber(step)} of 4
            </span>
          </div>
          <button
            onClick={dismiss}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Skip for now
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {step === "welcome" && (
            <WelcomeStep
              onNext={() => setStep(providers.length > 0 ? "camera" : "provider")}
              hasProvider={providers.length > 0}
            />
          )}
          {step === "provider" && (
            <ProviderStep
              presets={PROVIDER_PRESETS}
              presetIdx={presetIdx}
              setPresetIdx={setPresetIdx}
              providerName={providerName}
              setProviderName={setProviderName}
              providerApiKey={providerApiKey}
              setProviderApiKey={setProviderApiKey}
              providerModel={providerModel}
              setProviderModel={setProviderModel}
              error={providerError}
              testMsg={providerTestMsg}
              submitting={providerSubmitting}
              skipProvider={skipProvider}
              setSkipProvider={setSkipProvider}
            />
          )}
          {step === "camera" && (
            <CameraStep
              camName={camName}
              setCamName={setCamName}
              camStreamType={camStreamType}
              setCamStreamType={setCamStreamType}
              camStreamUrl={camStreamUrl}
              setCamStreamUrl={setCamStreamUrl}
              camLocation={camLocation}
              setCamLocation={setCamLocation}
              camPersonaId={camPersonaId}
              setCamPersonaId={setCamPersonaId}
              error={camError}
              submitting={camSubmitting}
            />
          )}
          {step === "summary" && (
            <SummaryStep
              persona={persona}
              providerName={providers[0]?.name || providerName}
              camName={camName}
            />
          )}
          {step === "done" && (
            <DoneStep onClose={() => {
              markDismissed();
              onComplete();
            }} />
          )}
        </div>

        <div className="px-5 py-3 border-t border-border flex items-center justify-between">
          <button
            onClick={() => {
              if (step === "provider") setStep("welcome");
              else if (step === "camera") setStep(providers.length > 0 ? "welcome" : "provider");
              else if (step === "summary") setStep("camera");
            }}
            className={`px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted ${
              step === "welcome" || step === "done" ? "invisible" : ""
            }`}
          >
            Back
          </button>
          {step === "welcome" && (
            <button
              onClick={() => setStep(providers.length > 0 ? "camera" : "provider")}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90"
            >
              Get started
            </button>
          )}
          {step === "provider" && (
            <button
              onClick={async () => {
                if (skipProvider) {
                  setStep("camera");
                  return;
                }
                // Second click after a failed test = proceed anyway.
                if (providerForceAdvance) {
                  setStep("camera");
                  return;
                }
                // Create the row if not already created, then test it.
                let pid = createdProviderId;
                if (!pid) {
                  const created = await createProvider();
                  if (!created) return;
                  pid = created.id;
                }
                const ok = await testProvider(pid);
                if (ok) {
                  setStep("camera");
                } else {
                  // Allow the next click to advance past the failure.
                  setProviderForceAdvance(true);
                }
              }}
              disabled={providerSubmitting}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50"
            >
              {skipProvider
                ? "Skip"
                : providerSubmitting
                ? "Adding."
                : providerForceAdvance
                ? "Continue anyway"
                : "Add & test"}
            </button>
          )}
          {step === "camera" && (
            <button
              onClick={async () => {
                if (!camStreamUrl.trim()) {
                  setCamError("Stream URL is required");
                  return;
                }
                const ok = await createCamera();
                if (ok) setStep("summary");
              }}
              disabled={camSubmitting}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50"
            >
              {camSubmitting ? "Adding." : "Add camera"}
            </button>
          )}
          {step === "summary" && (
            <button
              onClick={() => setStep("done")}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90"
            >
              Finish setup
            </button>
          )}
          {step === "done" && (
            <button
              onClick={() => {
                markDismissed();
                onComplete();
              }}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90"
            >
              Open dashboard
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function stepNumber(s: Step): number {
  if (s === "welcome") return 1;
  if (s === "provider") return 2;
  if (s === "camera") return 3;
  return 4;
}

function WelcomeStep({
  onNext,
  hasProvider,
}: {
  onNext: () => void;
  hasProvider: boolean;
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-xl font-semibold">Welcome to Nurby</h3>
      <p className="text-sm text-muted-foreground leading-relaxed">
        Five minutes to a working camera. We&apos;ll connect a vision
        model, add your first camera with a sensible preset, and pick
        a recap mode. You can change anything later from Settings.
      </p>
      <ul className="text-xs text-muted-foreground space-y-1 pl-4">
        <li>· Local Ollama works fully offline. No data leaves your network.</li>
        <li>· Cloud providers (OpenAI, Claude, Gemini) need API keys.</li>
        <li>· Each camera can use a different model and language.</li>
      </ul>
      {hasProvider && (
        <div className="text-xs text-muted-foreground bg-muted/50 px-3 py-2 rounded-md border border-border">
          You already have a provider configured. We&apos;ll skip step 2.
        </div>
      )}
      <button
        type="button"
        onClick={onNext}
        className="w-full mt-4 px-4 py-2 text-sm rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90"
      >
        Get started
      </button>
    </div>
  );
}

function ProviderStep({
  presets,
  presetIdx,
  setPresetIdx,
  providerName,
  setProviderName,
  providerApiKey,
  setProviderApiKey,
  providerModel,
  setProviderModel,
  error,
  testMsg,
  submitting,
  skipProvider,
  setSkipProvider,
}: {
  presets: typeof PROVIDER_PRESETS;
  presetIdx: number;
  setPresetIdx: (i: number) => void;
  providerName: string;
  setProviderName: (s: string) => void;
  providerApiKey: string;
  setProviderApiKey: (s: string) => void;
  providerModel: string;
  setProviderModel: (s: string) => void;
  error: string | null;
  testMsg: string | null;
  submitting: boolean;
  skipProvider: boolean;
  setSkipProvider: (b: boolean) => void;
}) {
  const preset = presets[presetIdx];
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold mb-1">Pick a vision model</h3>
        <p className="text-xs text-muted-foreground">
          The VLM describes what cameras see. You can connect more later.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {presets.map((p, i) => (
          <button
            key={p.kind}
            type="button"
            onClick={() => setPresetIdx(i)}
            className={`text-left p-3 rounded-lg border transition-colors ${
              presetIdx === i && !skipProvider
                ? "border-accent bg-accent/10"
                : "border-border hover:border-muted-foreground"
            }`}
          >
            <div className="font-medium text-sm">{p.name}</div>
            <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
              {p.hint}
            </p>
          </button>
        ))}
      </div>

      {!skipProvider && (
        <div className="space-y-3">
          <FieldRow label="Display name">
            <input
              type="text"
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow label="Base URL" hint="Auto-filled from preset">
            <input
              type="text"
              value={preset.base_url}
              readOnly
              className="w-full px-3 py-2 rounded-md bg-muted/30 border border-border text-sm font-mono opacity-70"
            />
          </FieldRow>
          <FieldRow label="Model" hint="The model name the provider uses by default. Override here if you want a different one.">
            <input
              type="text"
              value={providerModel}
              onChange={(e) => setProviderModel(e.target.value)}
              placeholder={preset.default_model}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
            />
          </FieldRow>
          {preset.keyRequired && (
            <FieldRow label="API key" hint="Stored encrypted on the server. Never sent to other providers.">
              <input
                type="password"
                value={providerApiKey}
                onChange={(e) => setProviderApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
              />
            </FieldRow>
          )}
          {preset.keyRequired ? (
            <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-300/90 leading-relaxed">
              Cloud providers bill per call. Nurby caps Ask-Nurby spend with a
              per-user daily budget (default $5/day, adjustable in Settings), and
              the perception pipeline only calls the model on real motion, so
              idle cameras cost nothing.
            </div>
          ) : (
            <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[11px] text-emerald-300/90 leading-relaxed">
              Local models are free and private. nothing leaves your network. This
              model captions what cameras see. For Ask-Nurby you&apos;ll also want a
              tool-capable local model (e.g. qwen2.5:3b) which you can deploy from
              Settings → Local AI.
            </div>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={skipProvider}
          onChange={(e) => setSkipProvider(e.target.checked)}
          className="accent-accent"
        />
        Skip this. I&apos;ll connect a provider later.
      </label>

      {error && <div className="text-xs text-danger">{error}</div>}
      {testMsg && (
        <div
          className={`text-xs ${
            testMsg.startsWith("Connected")
              ? "text-emerald-400"
              : testMsg.startsWith("Testing")
              ? "text-muted-foreground"
              : "text-amber-400"
          }`}
        >
          {testMsg}
        </div>
      )}
    </div>
  );
}

function CameraStep({
  camName,
  setCamName,
  camStreamType,
  setCamStreamType,
  camStreamUrl,
  setCamStreamUrl,
  camLocation,
  setCamLocation,
  camPersonaId,
  setCamPersonaId,
  error,
  submitting,
}: {
  camName: string;
  setCamName: (s: string) => void;
  camStreamType: string;
  setCamStreamType: (s: string) => void;
  camStreamUrl: string;
  setCamStreamUrl: (s: string) => void;
  camLocation: string;
  setCamLocation: (s: string) => void;
  camPersonaId: string;
  setCamPersonaId: (s: string) => void;
  error: string | null;
  submitting: boolean;
}) {
  const streamPreset = STREAM_TYPES.find((s) => s.value === camStreamType);
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold mb-1">Add your first camera</h3>
        <p className="text-xs text-muted-foreground">
          Pick a persona to fill the detection, recording, and summary
          settings in one click.
        </p>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground block mb-1.5">
          Persona
        </label>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {CAMERA_PERSONAS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setCamPersonaId(p.id)}
              className={`text-left p-2.5 rounded-lg border transition-colors ${
                camPersonaId === p.id
                  ? "border-accent bg-accent/10"
                  : "border-border hover:border-muted-foreground"
              }`}
            >
              <div className="font-medium text-xs">{p.label}</div>
              <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed line-clamp-2">
                {p.hint}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <FieldRow label="Name">
          <input
            type="text"
            value={camName}
            onChange={(e) => setCamName(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
          />
        </FieldRow>

        <FieldRow label="Stream type">
          <select
            value={camStreamType}
            onChange={(e) => setCamStreamType(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
          >
            {STREAM_TYPES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </FieldRow>

        <FieldRow label="Stream URL">
          <input
            type="text"
            value={camStreamUrl}
            onChange={(e) => setCamStreamUrl(e.target.value)}
            placeholder={streamPreset?.placeholder}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
          />
        </FieldRow>

        <FieldRow label="Location" hint="Where this camera is, e.g. Front porch">
          <input
            type="text"
            value={camLocation}
            onChange={(e) => setCamLocation(e.target.value)}
            placeholder="Front porch"
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
          />
        </FieldRow>
      </div>

      {error && <div className="text-xs text-danger">{error}</div>}
    </div>
  );
}

function SummaryStep({
  persona,
  providerName,
  camName,
}: {
  persona: ReturnType<typeof CAMERA_PERSONAS.find>;
  providerName: string;
  camName: string;
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold">Almost there</h3>
      <div className="rounded-lg border border-emerald-700/40 bg-emerald-950/15 p-3 space-y-1 text-xs">
        <div className="font-medium text-emerald-300 uppercase tracking-wider text-[10px] mb-1">
          What&apos;s configured
        </div>
        <div>· Provider: {providerName || "(none)"}</div>
        <div>· Camera: {camName}</div>
        {persona && <div>· Persona: {persona.label}</div>}
        {persona?.patch.summary_mode && persona.patch.summary_mode !== "off" && (
          <div>· Summary mode: {persona.patch.summary_mode}</div>
        )}
        {persona?.patch.audio_capture_enabled && (
          <div>· Audio: capture {persona.patch.audio_transcribe_enabled ? "+ transcribe" : ""}</div>
        )}
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        You can fine-tune any of these from the camera page. Add more
        cameras from the dashboard. Live captions and recap cards
        appear automatically as your perception worker processes frames.
      </p>
    </div>
  );
}

function DoneStep({ onClose }: { onClose: () => void }) {
  return (
    <div className="space-y-4 text-center py-6">
      <div className="w-12 h-12 rounded-full bg-emerald-500/15 border border-emerald-500/40 flex items-center justify-center mx-auto">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-400">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      </div>
      <h3 className="text-lg font-semibold">You&apos;re set</h3>
      <p className="text-xs text-muted-foreground max-w-md mx-auto leading-relaxed">
        Your camera is connected. The perception worker will start
        sending observations and audio captions as soon as motion or
        speech is detected. Check the timeline to see them.
      </p>
      <button
        type="button"
        onClick={onClose}
        className="px-4 py-2 text-sm rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90"
      >
        Open dashboard
      </button>
    </div>
  );
}

function FieldRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="text-xs font-medium text-muted-foreground block mb-1">
        {label}
      </label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground mt-1">{hint}</p>}
    </div>
  );
}
