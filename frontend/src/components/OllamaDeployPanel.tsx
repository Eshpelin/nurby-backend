"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";

interface VisionModel {
  name: string;
  label: string;
  family: string;
  ram_gb: number;
  quality: string;
  description: string;
}

interface OllamaStatus {
  installed: boolean;
  running: boolean;
  models: string[];
  recommended_model: string | null;
  system_ram_gb: number | null;
  available_models: VisionModel[];
  reachable_url: string | null;
}

export interface OllamaDeployPanelProps {
  // Called after the provider is provisioned (deployed or reused). The
  // parent refreshes providers and advances the wizard.
  onProvisioned: () => void;
}

// Detects an existing Ollama (local or on the Docker host), lets the
// user reuse an already-installed vision model, or pulls a recommended
// one for the detected RAM. Falls back to a clear message + the URL
// field when no Ollama is reachable.
export function OllamaDeployPanel({ onProvisioned }: OllamaDeployPanelProps) {
  const { authFetch } = useAuth();
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    authFetch("/api/ollama/status")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((s: OllamaStatus) => {
        if (cancelled) return;
        setStatus(s);
        setModel(s.recommended_model || s.available_models[0]?.name || "");
      })
      .catch(() => {
        if (!cancelled) setStatusError("Could not reach the Ollama status endpoint.");
      });
    return () => {
      cancelled = true;
    };
  }, [authFetch]);

  // Vision models that are already pulled on the detected Ollama.
  const installedVision = useMemo(() => {
    if (!status) return [] as VisionModel[];
    const names = new Set(status.models);
    const known = status.available_models.filter((m) => names.has(m.name));
    // Surface any other installed model too (in case it isn't in our list).
    const extra = status.models
      .filter((n) => !status.available_models.some((m) => m.name === n))
      .map((n) => ({ name: n, label: n, family: "", ram_gb: 0, quality: "", description: "" }));
    return [...known, ...extra];
  }, [status]);

  const [reuseModel, setReuseModel] = useState("");
  useEffect(() => {
    if (installedVision.length && !reuseModel) setReuseModel(installedVision[0].name);
  }, [installedVision, reuseModel]);

  async function createProviderAt(baseUrl: string, modelName: string) {
    const res = await authFetch("/api/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `Ollama (${modelName})`,
        kind: "ollama",
        base_url: baseUrl,
        default_model: modelName,
        active: true,
      }),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || `Failed to add provider (${res.status})`);
    }
  }

  const useExisting = async () => {
    if (!status?.reachable_url || !reuseModel) return;
    setBusy(true);
    setError("");
    setMsg("Connecting to your existing Ollama.");
    try {
      await createProviderAt(status.reachable_url, reuseModel);
      onProvisioned();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
      setMsg("");
    } finally {
      setBusy(false);
    }
  };

  const deploy = async () => {
    if (!model) return;
    setBusy(true);
    setError("");
    setMsg("Deploying. Starting Ollama and pulling the model. This can take a few minutes on first run.");
    try {
      const res = await authFetch("/api/ollama/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.stage === "done") {
        onProvisioned();
        return;
      }
      setError(data.message || `Deploy failed (${res.status})`);
      setMsg("");
    } catch {
      setError("Network error during deploy");
      setMsg("");
    } finally {
      setBusy(false);
    }
  };

  if (statusError) {
    return (
      <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-300/90">
        {statusError} You can still point to an Ollama running elsewhere using the
        Base URL field below.
      </div>
    );
  }
  if (!status) {
    return <div className="text-[11px] text-muted-foreground">Checking for a local AI.</div>;
  }

  // Case A. an Ollama is reachable. Reuse it.
  const detectedBlock = status.running && status.reachable_url && (
    <div className="rounded-md border border-emerald-500/25 bg-emerald-500/[0.05] px-3 py-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-emerald-200">Existing Ollama detected</div>
        <div className="text-[10px] text-muted-foreground font-mono">{status.reachable_url}</div>
      </div>
      {installedVision.length > 0 ? (
        <>
          <select
            value={reuseModel}
            onChange={(e) => setReuseModel(e.target.value)}
            disabled={busy}
            className="w-full px-2 py-1.5 rounded-md bg-background border border-border text-xs"
          >
            {installedVision.map((m) => (
              <option key={m.name} value={m.name}>
                {m.label}
                {m.ram_gb ? ` . ~${m.ram_gb} GB` : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={useExisting}
            disabled={busy || !reuseModel}
            className="w-full px-3 py-1.5 text-xs rounded-md bg-emerald-500/90 text-black font-medium hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Connecting." : `Use ${reuseModel}`}
          </button>
        </>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          Connected, but no vision model is installed yet. Pull one below.
        </p>
      )}
    </div>
  );

  // Case B. we can pull a model (the server has the Ollama binary).
  const deployBlock = status.installed && (
    <div className="rounded-md border border-border bg-background/40 px-3 py-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium">
          {status.running ? "Pull another model" : "Deploy a local model"}
        </div>
        {status.system_ram_gb != null && (
          <div className="text-[10px] text-muted-foreground">{status.system_ram_gb} GB RAM</div>
        )}
      </div>
      <select
        value={model}
        onChange={(e) => setModel(e.target.value)}
        disabled={busy}
        className="w-full px-2 py-1.5 rounded-md bg-background border border-border text-xs"
      >
        {status.available_models.map((m) => (
          <option key={m.name} value={m.name}>
            {m.label} . ~{m.ram_gb} GB . {m.quality}
            {m.name === status.recommended_model ? " (recommended)" : ""}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={deploy}
        disabled={busy || !model}
        className="w-full px-3 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
      >
        {busy ? "Working." : `Deploy ${model}`}
      </button>
    </div>
  );

  // Case C. nothing reachable and no local binary.
  const noneBlock = !status.running && !status.installed && (
    <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2.5 text-[11px] text-amber-300/90 leading-relaxed space-y-1">
      <div className="font-medium text-amber-200">No Ollama detected.</div>
      <div>
        Install Ollama (
        <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className="underline">
          ollama.com/download
        </a>
        ) on the machine or host that runs Nurby. If it runs in Docker, start Ollama on the
        host and point the Base URL below at{" "}
        <span className="font-mono">http://host.docker.internal:11434</span>.
      </div>
    </div>
  );

  return (
    <div className="space-y-2">
      {detectedBlock}
      {deployBlock}
      {noneBlock}
      {error && <div className="text-[11px] text-red-400">{error}</div>}
      {msg && <div className="text-[11px] text-emerald-300/90">{msg}</div>}
    </div>
  );
}

export default OllamaDeployPanel;
