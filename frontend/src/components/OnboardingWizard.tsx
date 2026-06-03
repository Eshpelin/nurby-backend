"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import CameraBrandHelp from "@/components/CameraBrandHelp";
import { OllamaDeployPanel } from "@/components/OllamaDeployPanel";
import { AddCameraModal } from "@/components/AddCameraModal";

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

type Step = "camera" | "provider" | "done";

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


/**
 * Three-step first-run modal, ordered for the fastest path to a live feed:
 *   1. camera  (demo camera is the default. one click and you're watching)
 *   2. provider (optional VLM. detection, faces and rules work without it,
 *      so this step defaults to a pure Next)
 *   3. done
 *
 * Every step is skippable. Completing the wizard sets a localStorage flag
 * so it does not pop up again. The dashboard decides when to mount this
 * (see /app/page.tsx).
 */
export function OnboardingWizard({ onClose, onComplete }: Props) {
  const { authFetch } = useAuth();
  const [step, setStep] = useState<Step>("camera");
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
  const [providerBaseUrl, setProviderBaseUrl] = useState<string>(PROVIDER_PRESETS[0].base_url);
  const [providerSubmitting, setProviderSubmitting] = useState(false);
  const [providerError, setProviderError] = useState<string | null>(null);
  // Connection-test state. After we create the provider row we hit the
  // backend test endpoint so a wrong key / unreachable endpoint fails
  // fast in the wizard instead of silently later. ForceAdvance lets the
  // user proceed past a failed test on a second click.
  const [providerTestMsg, setProviderTestMsg] = useState<string | null>(null);
  const [providerForceAdvance, setProviderForceAdvance] = useState(false);
  const [createdProviderId, setCreatedProviderId] = useState<string | null>(null);
  // Default to skipping. A VLM only adds scene captions and Ask. YOLO
  // detection, faces, people and rules all run locally without it, so the
  // honest default for a 3-click setup is "Next" straight past this step.
  const [skipProvider, setSkipProvider] = useState(true);


  const preset = PROVIDER_PRESETS[presetIdx];

  // Auto-pick provider name + default model + base url from preset.
  useEffect(() => {
    setProviderName(PROVIDER_PRESETS[presetIdx].name);
    setProviderModel(PROVIDER_PRESETS[presetIdx].default_model);
    setProviderBaseUrl(PROVIDER_PRESETS[presetIdx].base_url);
  }, [presetIdx]);

  // The Ollama deploy endpoint auto-creates the provider. Refresh the
  // provider list and finish, since this is the last meaningful step.
  async function onOllamaProvisioned() {
    try {
      const r = await authFetch("/api/providers");
      if (r.ok) setProviders(await r.json());
    } catch {
      /* non-fatal. The provider was created server-side regardless */
    }
    setStep("done");
  }

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
        base_url: providerBaseUrl.trim() || preset.base_url,
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
              Step {stepNumber(step)} of 3
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
          {step === "camera" && (
            <CameraStep onAdded={() => setStep("provider")} />
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
              providerBaseUrl={providerBaseUrl}
              setProviderBaseUrl={setProviderBaseUrl}
              onProvisioned={onOllamaProvisioned}
              error={providerError}
              testMsg={providerTestMsg}
              submitting={providerSubmitting}
              skipProvider={skipProvider}
              setSkipProvider={setSkipProvider}
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
              if (step === "provider") setStep("camera");
            }}
            className={`px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted ${
              step === "camera" || step === "done" ? "invisible" : ""
            }`}
          >
            Back
          </button>
          {step === "camera" && (
            <button
              onClick={() => setStep("provider")}
              className="px-4 py-1.5 text-xs rounded-md border border-border hover:bg-muted text-muted-foreground"
            >
              Skip for now
            </button>
          )}
          {step === "provider" && (
            <button
              onClick={async () => {
                if (skipProvider) {
                  setStep("done");
                  return;
                }
                // Second click after a failed test = proceed anyway.
                if (providerForceAdvance) {
                  setStep("done");
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
                  setStep("done");
                } else {
                  // Allow the next click to advance past the failure.
                  setProviderForceAdvance(true);
                }
              }}
              disabled={providerSubmitting}
              className="px-4 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50"
            >
              {skipProvider
                ? "Skip & finish"
                : providerSubmitting
                ? "Adding."
                : providerForceAdvance
                ? "Continue anyway"
                : "Add & test"}
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
  if (s === "camera") return 1;
  if (s === "provider") return 2;
  return 3;
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
  providerBaseUrl,
  setProviderBaseUrl,
  onProvisioned,
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
  providerBaseUrl: string;
  setProviderBaseUrl: (s: string) => void;
  onProvisioned: () => void;
  error: string | null;
  testMsg: string | null;
  submitting: boolean;
  skipProvider: boolean;
  setSkipProvider: (b: boolean) => void;
}) {
  const preset = presets[presetIdx];
  const isOllama = preset.kind === "ollama";
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold mb-1">
          Add a vision model <span className="text-muted-foreground font-normal">(optional)</span>
        </h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Detection, faces, people and rules already work without this. A
          vision model adds plain-language scene captions and lets you Ask
          Nurby questions. Skip it now and add one anytime from Settings.
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

      {!skipProvider && isOllama && (
        <OllamaDeployPanel onProvisioned={onProvisioned} />
      )}

      {!skipProvider && (
        <div className="space-y-3">
          {isOllama && (
            <p className="text-[11px] text-muted-foreground -mb-1">
              Or connect to an Ollama that is already running somewhere on your network.
            </p>
          )}
          <FieldRow label="Display name">
            <input
              type="text"
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
            />
          </FieldRow>
          <FieldRow
            label="Base URL"
            hint={isOllama ? "Where Ollama is reachable. E.g. http://host.docker.internal:11434 from Docker." : "Auto-filled from preset"}
          >
            <input
              type="text"
              value={providerBaseUrl}
              onChange={(e) => setProviderBaseUrl(e.target.value)}
              readOnly={!isOllama}
              className={`w-full px-3 py-2 rounded-md border border-border text-sm font-mono ${
                isOllama
                  ? "bg-background focus:outline-none focus:border-accent"
                  : "bg-muted/30 opacity-70"
              }`}
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
              Local models are free and private. Nothing leaves your network. This
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

function CameraStep({ onAdded }: { onAdded: () => void }) {
  const { authFetch } = useAuth();
  const [mode, setMode] = useState<"demo" | "own">("demo");
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoError, setDemoError] = useState("");

  const useDemo = async () => {
    setDemoBusy(true);
    setDemoError("");
    try {
      const r = await authFetch("/api/cameras/demo", { method: "POST" });
      if (!r.ok) {
        setDemoError("Could not add the demo camera. You can add a real one instead.");
        return;
      }
      onAdded();
    } catch {
      setDemoError("Network error adding the demo camera.");
    } finally {
      setDemoBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold mb-1">Add your first camera</h3>
        <p className="text-xs text-muted-foreground">
          No camera yet? Start with the demo feed and see Nurby work in seconds. You can connect real cameras anytime.
        </p>
      </div>

      <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium">Demo camera</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent">recommended</span>
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          Streams looping sample CCTV footage through the full pipeline, so you can watch detections, people, and rules with zero setup.
        </p>
        {demoError && <div className="text-[11px] text-red-400">{demoError}</div>}
        <button
          type="button"
          onClick={useDemo}
          disabled={demoBusy}
          className="px-3 py-1.5 text-xs rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50"
        >
          {demoBusy ? "Adding." : "Use demo camera and continue"}
        </button>
      </div>

      <button
        type="button"
        onClick={() => setMode(mode === "own" ? "demo" : "own")}
        className="text-xs text-muted-foreground hover:text-foreground underline"
      >
        {mode === "own" ? "Hide" : "Or connect your own camera"}
      </button>

      {mode === "own" && (
        <div className="rounded-lg border border-border p-3">
          <AddCameraModal embedded onSuccess={onAdded} onClose={() => setMode("demo")} />
        </div>
      )}
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
        Your camera is connected. Give it ~30 seconds. Nurby starts
        describing activity as soon as it sees motion, and the first
        observations will land on your timeline.
      </p>
      <div className="text-left max-w-md mx-auto space-y-2">
        <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
          Two things worth doing next
        </div>
        <a
          href="/rules"
          className="flex items-start gap-3 rounded-md border border-border bg-card/40 px-3 py-2 hover:border-accent/50 transition-colors"
        >
          <span className="text-base leading-none">🔔</span>
          <span>
            <span className="block text-xs font-medium">Create your first rule</span>
            <span className="block text-[11px] text-muted-foreground leading-tight">
              Get a Telegram or email alert when something specific happens.
            </span>
          </span>
        </a>
        <a
          href="/ask"
          className="flex items-start gap-3 rounded-md border border-border bg-card/40 px-3 py-2 hover:border-accent/50 transition-colors"
        >
          <span className="text-base leading-none">💬</span>
          <span>
            <span className="block text-xs font-medium">Ask Nurby anything</span>
            <span className="block text-[11px] text-muted-foreground leading-tight">
              &ldquo;What happened today?&rdquo; &middot; &ldquo;Was anyone at the door?&rdquo;
            </span>
          </span>
        </a>
      </div>
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
