"use client";

import { useEffect, useState } from "react";
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
}

export interface OllamaDeployPanelProps {
  // Called after a successful deploy. the backend auto-creates the
  // Ollama provider, so the parent should refresh providers and advance.
  onProvisioned: () => void;
}

// One-click local model deployment for onboarding. Checks the server's
// Ollama status, recommends a model for the detected RAM, and pulls it.
// When Ollama is not available on the server (e.g. a dockerised API), it
// explains that and points the user to the URL fallback below.
export function OllamaDeployPanel({ onProvisioned }: OllamaDeployPanelProps) {
  const { authFetch } = useAuth();
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [model, setModel] = useState("");
  const [deploying, setDeploying] = useState(false);
  const [deployMsg, setDeployMsg] = useState("");
  const [deployError, setDeployError] = useState("");

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

  const deploy = async () => {
    if (!model) return;
    setDeploying(true);
    setDeployError("");
    setDeployMsg("Deploying. starting Ollama and pulling the model. This can take a few minutes on first run.");
    try {
      const res = await authFetch("/api/ollama/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.stage === "done") {
        setDeployMsg(data.message || "Model ready.");
        onProvisioned();
        return;
      }
      setDeployError(data.message || `Deploy failed (${res.status})`);
      setDeployMsg("");
    } catch {
      setDeployError("Network error during deploy");
      setDeployMsg("");
    } finally {
      setDeploying(false);
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
    return <div className="text-[11px] text-muted-foreground">Checking local AI.</div>;
  }

  // Ollama is not available where the Nurby server runs (common with a
  // dockerised API). Explain and let them use the URL fallback.
  if (!status.installed) {
    return (
      <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2.5 text-[11px] text-amber-300/90 leading-relaxed space-y-1">
        <div className="font-medium text-amber-200">Ollama is not running on the Nurby server.</div>
        <div>
          One-click deploy needs Ollama installed where the Nurby API runs. If you
          run the API in Docker, install Ollama on the host (
          <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className="underline">
            ollama.com/download
          </a>
          ) and point the Base URL below at it (e.g.{" "}
          <span className="font-mono">http://host.docker.internal:11434</span>).
        </div>
      </div>
    );
  }

  const alreadyHave = model && status.models.includes(model);

  return (
    <div className="rounded-md border border-emerald-500/20 bg-emerald-500/[0.04] px-3 py-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-emerald-200">Deploy a local model</div>
        {status.system_ram_gb != null && (
          <div className="text-[10px] text-muted-foreground">{status.system_ram_gb} GB RAM detected</div>
        )}
      </div>

      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground block">Model</label>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={deploying}
          className="w-full px-2 py-1.5 rounded-md bg-background border border-border text-xs"
        >
          {status.available_models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.label} . ~{m.ram_gb} GB . {m.quality}
              {m.name === status.recommended_model ? " (recommended)" : ""}
            </option>
          ))}
        </select>
        {model && (
          <p className="text-[10px] text-muted-foreground">
            {status.available_models.find((m) => m.name === model)?.description}
          </p>
        )}
      </div>

      {deployError && <div className="text-[11px] text-red-400">{deployError}</div>}
      {deployMsg && <div className="text-[11px] text-emerald-300/90">{deployMsg}</div>}

      <button
        type="button"
        onClick={deploy}
        disabled={deploying || !model}
        className="w-full px-3 py-1.5 text-xs rounded-md bg-emerald-500/90 text-black font-medium hover:opacity-90 disabled:opacity-50"
      >
        {deploying
          ? "Deploying."
          : alreadyHave
          ? `Use ${model} (already installed)`
          : `Deploy ${model}`}
      </button>
      <p className="text-[10px] text-muted-foreground">
        Pulls the model with Ollama on the Nurby server and configures it as your
        vision provider. Free, private, and offline.
      </p>
    </div>
  );
}

export default OllamaDeployPanel;
