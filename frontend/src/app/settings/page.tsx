"use client";

import { useCallback, useEffect, useState } from "react";

interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  default_model: string | null;
  active: boolean;
  created_at: string;
}

const PROVIDER_KINDS = [
  { value: "openai", label: "OpenAI-compatible" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google", label: "Google Gemini" },
  { value: "ollama", label: "Ollama" },
];

const ALL_PROVIDERS = [
  // Cloud providers
  { name: "OpenAI", kind: "openai", url: "https://api.openai.com", model: "gpt-4o-mini", description: "GPT-4o, GPT-4o-mini, o1", needsKey: true },
  { name: "Anthropic", kind: "anthropic", url: "https://api.anthropic.com", model: "claude-sonnet-4-20250514", description: "Claude Sonnet, Opus, Haiku", needsKey: true },
  { name: "Google Gemini", kind: "google", url: "https://generativelanguage.googleapis.com", model: "gemini-2.0-flash", description: "Gemini 2.0 Flash, Pro, Ultra", needsKey: true },
  { name: "Together AI", kind: "openai", url: "https://api.together.xyz", model: "meta-llama/Llama-3-70b-chat-hf", description: "Llama, Mixtral, Qwen, SDXL", needsKey: true },
  { name: "Groq", kind: "openai", url: "https://api.groq.com/openai", model: "llama-3.1-70b-versatile", description: "Ultra-fast Llama, Mixtral inference", needsKey: true },
  { name: "Fireworks AI", kind: "openai", url: "https://api.fireworks.ai/inference", model: "accounts/fireworks/models/llama-v3p1-70b-instruct", description: "Llama, Mixtral, FireFunction", needsKey: true },
  { name: "Mistral AI", kind: "openai", url: "https://api.mistral.ai", model: "mistral-large-latest", description: "Mistral Large, Medium, Small", needsKey: true },
  { name: "DeepSeek", kind: "openai", url: "https://api.deepseek.com", model: "deepseek-chat", description: "DeepSeek V3, R1", needsKey: true },
  { name: "OpenRouter", kind: "openai", url: "https://openrouter.ai/api", model: "openai/gpt-4o-mini", description: "Unified gateway to 200+ models", needsKey: true },
  { name: "Perplexity", kind: "openai", url: "https://api.perplexity.ai", model: "llama-3.1-sonar-large-128k-online", description: "Online search-grounded models", needsKey: true },
  // Local providers
  { name: "Ollama", kind: "ollama", url: "http://localhost:11434", model: "moondream", description: "Local models (moondream, llava, etc.)", needsKey: false },
  { name: "LMStudio", kind: "openai", url: "http://localhost:1234", model: "local-model", description: "Local OpenAI-compatible server", needsKey: false },
  { name: "vLLM", kind: "openai", url: "http://localhost:8000", model: "local-model", description: "High-throughput local serving", needsKey: false },
];

export default function SettingsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editProvider, setEditProvider] = useState<Provider | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; message: string; latency_ms?: number; models?: string[] }>>({});

  // Form
  const [formName, setFormName] = useState("");
  const [formKind, setFormKind] = useState("openai");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formModel, setFormModel] = useState("");
  const [formActive, setFormActive] = useState(true);
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const fetchProviders = useCallback(async () => {
    try {
      const res = await fetch("/api/providers");
      if (res.ok) setProviders(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const resetForm = () => {
    setFormName("");
    setFormKind("openai");
    setFormBaseUrl("");
    setFormApiKey("");
    setFormModel("");
    setFormActive(true);
    setFormError("");
  };

  const openCreate = (presetName?: string) => {
    setEditProvider(null);
    resetForm();
    if (presetName) {
      const preset = ALL_PROVIDERS.find((p) => p.name === presetName);
      if (preset) {
        setFormKind(preset.kind);
        setFormName(preset.name);
        setFormBaseUrl(preset.url);
        setFormModel(preset.model);
      }
    }
    setShowModal(true);
  };

  const openEdit = (p: Provider) => {
    setEditProvider(p);
    setFormName(p.name);
    setFormKind(p.kind);
    setFormBaseUrl(p.base_url);
    setFormApiKey("");
    setFormModel(p.default_model || "");
    setFormActive(p.active);
    setFormError("");
    setShowModal(true);
  };

  const handleKindChange = (kind: string) => {
    setFormKind(kind);
    if (!editProvider) {
      // Find first matching preset for this kind
      const preset = ALL_PROVIDERS.find((p) => p.kind === kind);
      if (preset) {
        const kindLabel = PROVIDER_KINDS.find((k) => k.value === kind)?.label || kind;
        if (!formName || ALL_PROVIDERS.some((p) => p.name === formName) || PROVIDER_KINDS.some((p) => p.label === formName)) {
          setFormName(kindLabel);
        }
        setFormBaseUrl(preset.url);
        setFormModel(preset.model);
      }
    }
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError("Name is required");
      return;
    }
    if (!formBaseUrl.trim()) {
      setFormError("Base URL is required");
      return;
    }
    const isLocal = formKind === "ollama" || formBaseUrl.includes("localhost") || formBaseUrl.includes("127.0.0.1");
    if (!isLocal && !formApiKey.trim() && !editProvider) {
      setFormError("API key is required for cloud providers");
      return;
    }

    setSubmitting(true);
    setFormError("");

    const body: Record<string, unknown> = {
      name: formName.trim(),
      kind: formKind,
      base_url: formBaseUrl.trim().replace(/\/+$/, ""),
      default_model: formModel.trim() || null,
      active: formActive,
    };
    if (formApiKey.trim()) {
      body.api_key = formApiKey.trim();
    }

    try {
      let res: Response;
      if (editProvider) {
        res = await fetch(`/api/providers/${editProvider.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        res = await fetch("/api/providers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setFormError(data.detail || "Failed to save provider");
        return;
      }

      setShowModal(false);
      fetchProviders();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/providers/${id}`, { method: "DELETE" });
      fetchProviders();
    } catch {
      /* silent */
    }
  };

  const handleTest = async (provider: Provider) => {
    setTestingId(provider.id);
    setTestResult((prev) => ({ ...prev, [provider.id]: undefined as never }));

    try {
      const res = await fetch(`/api/providers/${provider.id}/test`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setTestResult((prev) => ({ ...prev, [provider.id]: data }));
      } else {
        setTestResult((prev) => ({
          ...prev,
          [provider.id]: { ok: false, message: `Test endpoint returned ${res.status}` },
        }));
      }
    } catch {
      setTestResult((prev) => ({
        ...prev,
        [provider.id]: { ok: false, message: "Network error. Could not reach Nurby API" },
      }));
    } finally {
      setTestingId(null);
    }
  };

  const activeProvider = providers.find((p) => p.active);

  return (
    <div className="px-6 py-6 max-w-4xl">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Configure VLM providers for scene descriptions, search, and question answering
        </p>
      </div>

      {/* Active provider status */}
      <div className="rounded-lg border border-border bg-card p-4 mb-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                activeProvider ? "bg-green-500 pulse-dot" : "bg-yellow-500"
              }`}
            />
            <div>
              <div className="text-sm font-medium">
                {activeProvider
                  ? `Active provider. ${activeProvider.name}`
                  : "No active provider"}
              </div>
              <div className="text-xs text-muted-foreground">
                {activeProvider
                  ? `${activeProvider.kind} / ${activeProvider.default_model || "default model"}`
                  : "VLM features (scene descriptions, AI search, summaries) require a configured provider"}
              </div>
            </div>
          </div>
          {!activeProvider && (
            <button
              onClick={() => openCreate()}
              className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
            >
              + Add provider
            </button>
          )}
        </div>
      </div>

      {/* Provider presets */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium">
            {providers.length === 0 ? "Choose a provider to get started" : "Add another provider"}
          </h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {ALL_PROVIDERS.map((preset) => (
            <button
              key={preset.name}
              onClick={() => openCreate(preset.name)}
              className="rounded-lg border border-border bg-card p-3 text-left hover:border-accent/50 transition-colors group"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-sm group-hover:text-accent transition-colors">
                  {preset.name}
                </span>
                {!preset.needsKey && (
                  <span className="text-[9px] px-1 py-0.5 rounded bg-green-900/30 text-green-400 border border-green-800/40">
                    local
                  </span>
                )}
              </div>
              <div className="text-[11px] text-muted-foreground leading-snug">{preset.description}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Provider list */}
      {loading ? (
        <div className="text-sm text-muted-foreground py-10 text-center">Loading.</div>
      ) : providers.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Configured providers</h2>
            <button
              onClick={() => openCreate()}
              className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
            >
              + Add another
            </button>
          </div>
          {providers.map((p) => (
            <div
              key={p.id}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      p.active ? "bg-green-500" : "bg-muted-foreground/40"
                    }`}
                  />
                  <div>
                    <div className="font-medium text-sm">{p.name}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {p.kind} / {p.base_url}
                    </div>
                    {p.default_model && (
                      <div className="text-xs text-muted-foreground">
                        Model. {p.default_model}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={() => handleTest(p)}
                    disabled={testingId === p.id}
                    className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    {testingId === p.id ? "Testing." : "Test"}
                  </button>
                  <button
                    onClick={() => openEdit(p)}
                    className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(p.id)}
                    className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors"
                  >
                    Del
                  </button>
                </div>
              </div>
              {testResult[p.id] && (
                <div
                  className={`mt-2 text-xs px-2 py-2 rounded space-y-1 ${
                    testResult[p.id].ok
                      ? "bg-green-900/20 text-green-400"
                      : "bg-red-900/20 text-red-400"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span>{testResult[p.id].message}</span>
                    {testResult[p.id].latency_ms != null && (
                      <span className="font-mono text-[10px] opacity-70">
                        {testResult[p.id].latency_ms}ms
                      </span>
                    )}
                  </div>
                  {testResult[p.id].models && testResult[p.id].models!.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {testResult[p.id].models!.slice(0, 8).map((m) => (
                        <span
                          key={m}
                          className="px-1 py-0.5 text-[10px] rounded bg-black/20 font-mono"
                        >
                          {m}
                        </span>
                      ))}
                      {testResult[p.id].models!.length > 8 && (
                        <span className="text-[10px] opacity-70">
                          +{testResult[p.id].models!.length - 8} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : null}

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowModal(false)}
          />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold mb-4">
              {editProvider ? "Edit provider" : "Add VLM provider"}
            </h2>

            <div className="space-y-3">
              {/* Kind */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Provider type
                </label>
                <div className="grid grid-cols-4 gap-1">
                  {PROVIDER_KINDS.map((pk) => (
                    <button
                      key={pk.value}
                      onClick={() => handleKindChange(pk.value)}
                      className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                        formKind === pk.value
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      {pk.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Kind hint */}
              {formKind === "openai" && (
                <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                  OpenAI-compatible API. Works with OpenAI, Gemini, Together, Groq, Fireworks, Mistral, DeepSeek, LMStudio, vLLM, and any provider that implements the /v1/chat/completions endpoint.
                </div>
              )}

              {/* Name */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Display name
                </label>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                  placeholder="My OpenAI"
                />
              </div>

              {/* Base URL */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Base URL
                </label>
                <input
                  type="url"
                  value={formBaseUrl}
                  onChange={(e) => setFormBaseUrl(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                  placeholder="https://api.openai.com"
                />
                <span className="text-[10px] text-muted-foreground">
                  {formKind === "ollama" && "Default. http://localhost:11434"}
                  {formKind === "openai" && "Most providers use OpenAI-compatible APIs. Select a preset above to prefill."}
                  {formKind === "google" && "Default. https://generativelanguage.googleapis.com"}
                  {formKind === "anthropic" && "Default. https://api.anthropic.com"}
                </span>
              </div>

              {/* API Key */}
              {!(formKind === "ollama" || formBaseUrl.includes("localhost") || formBaseUrl.includes("127.0.0.1")) && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">
                    API key
                  </label>
                  <input
                    type="password"
                    value={formApiKey}
                    onChange={(e) => setFormApiKey(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                    placeholder={editProvider ? "Leave blank to keep existing key" : "sk-..."}
                  />
                </div>
              )}

              {/* Model */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Default model
                </label>
                <input
                  type="text"
                  value={formModel}
                  onChange={(e) => setFormModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                  placeholder={
                    formKind === "openai"
                      ? "gpt-4o-mini"
                      : formKind === "anthropic"
                      ? "claude-sonnet-4-20250514"
                      : "moondream"
                  }
                />
              </div>

              {/* Active */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formActive}
                  onChange={(e) => setFormActive(e.target.checked)}
                  className="accent-green-500"
                />
                <span className="text-sm">Active (used for VLM calls)</span>
              </label>

              {formError && (
                <div className="text-xs text-red-400">{formError}</div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => setShowModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting}
                className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
              >
                {submitting ? "Saving." : editProvider ? "Save" : "Add provider"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
