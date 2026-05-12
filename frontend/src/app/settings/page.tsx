"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  default_model: string | null;
  active: boolean;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  created_at: string;
}

interface InviteKey {
  id: string;
  key: string;
  role: string;
  camera_ids: string[] | null;
  max_uses: number;
  use_count: number;
  expires_at: string | null;
  created_at: string;
}

interface Camera {
  id: string;
  name: string;
}

interface CameraStorage {
  camera_id: string;
  camera_name: string;
  recording_count: number;
  recording_bytes: number;
  observation_count: number;
  retention_mode: string;
  retention_days: number;
  retention_gb: number;
}

interface StorageStats {
  cameras: CameraStorage[];
  total_recording_bytes: number;
  total_observations: number;
}

const PROVIDER_KINDS = [
  { value: "openai", label: "OpenAI-compatible" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google", label: "Google Gemini" },
  { value: "ollama", label: "Ollama" },
];

const ALL_PROVIDERS = [
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
  { name: "Ollama", kind: "ollama", url: "http://localhost:11434", model: "moondream", description: "Local models (moondream, llava, etc.)", needsKey: false },
  { name: "LMStudio", kind: "openai", url: "http://localhost:1234", model: "local-model", description: "Local OpenAI-compatible server", needsKey: false },
  { name: "vLLM", kind: "openai", url: "http://localhost:8000", model: "local-model", description: "High-throughput local serving", needsKey: false },
];

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function retentionLabel(cam: CameraStorage): string {
  if (cam.retention_mode === "time") return "Keep " + cam.retention_days + " days";
  if (cam.retention_mode === "size") return "Max " + cam.retention_gb + " GB";
  return "No limit";
}

function usagePercent(cam: CameraStorage): number | null {
  if (cam.retention_mode === "size" && cam.retention_gb > 0) {
    const limitBytes = cam.retention_gb * 1024 * 1024 * 1024;
    return (cam.recording_bytes / limitBytes) * 100;
  }
  return null;
}

function barColor(percent: number | null): string {
  if (percent === null) return "bg-blue-500";
  if (percent >= 80) return "bg-red-500";
  if (percent >= 50) return "bg-yellow-500";
  return "bg-green-500";
}

export default function SettingsPage() {
  const { authFetch, token } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [showProviderModal, setShowProviderModal] = useState(false);
  const [editProvider, setEditProvider] = useState<Provider | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; message: string; latency_ms?: number; models?: string[] }>>({});

  // Expanded sections
  const [showProviders, setShowProviders] = useState(false);
  const [showStorage, setShowStorage] = useState(false);

  // Invite key state
  const [inviteKeys, setInviteKeys] = useState<InviteKey[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [inviteRole, setInviteRole] = useState("viewer");
  const [inviteCameraIds, setInviteCameraIds] = useState<string[]>([]);
  const [inviteMaxUses, setInviteMaxUses] = useState(1);
  const [inviteCreating, setInviteCreating] = useState(false);

  // Storage state
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

  // SMTP state
  const [smtpConfig, setSmtpConfig] = useState<{
    host: string; port: string; user: string; password: string; from: string; tls: boolean;
  }>({
    host: "", port: "587", user: "", password: "", from: "", tls: true,
  });
  const [smtpLoading, setSmtpLoading] = useState(true);
  const [showSmtpModal, setShowSmtpModal] = useState(false);
  const [smtpTestEmail, setSmtpTestEmail] = useState("");
  const [smtpTesting, setSmtpTesting] = useState(false);
  const [smtpTestResult, setSmtpTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Privacy blur state (per-person toggle)
  const [blurPersons, setBlurPersons] = useState<{ id: string; display_name: string; relationship: string | null; privacy_blur: boolean; photo_path: string | null }[]>([]);
  const [blurLoading, setBlurLoading] = useState(true);
  const [showBlurModal, setShowBlurModal] = useState(false);
  const [blurSavingId, setBlurSavingId] = useState<string | null>(null);

  // Nudity blur (global flag)
  const [nudityBlur, setNudityBlur] = useState<boolean>(true);
  const [nudityMinScore, setNudityMinScore] = useState<number>(0.5);
  const [nudityLoading, setNudityLoading] = useState<boolean>(true);
  const [nuditySaving, setNuditySaving] = useState<boolean>(false);
  const [journeyIdleSeconds, setJourneyIdleSeconds] = useState<number>(300);
  const [dailyDigestEnabled, setDailyDigestEnabled] = useState<boolean>(true);
  const [dailyDigestHour, setDailyDigestHour] = useState<number>(7);
  const [dailyDigestProviderId, setDailyDigestProviderId] = useState<string>("");
  const [extraSaving, setExtraSaving] = useState<boolean>(false);

  // Ollama auto-deploy state
  const [ollamaStatus, setOllamaStatus] = useState<{
    installed: boolean; running: boolean; models: string[];
    recommended_model: string | null; system_ram_gb: number | null;
    available_models: { name: string; label: string; family: string; ram_gb: number; quality: string; vision: boolean; description: string }[];
  } | null>(null);
  const [ollamaChecking, setOllamaChecking] = useState(false);
  const [ollamaDeploying, setOllamaDeploying] = useState(false);
  const [ollamaSelectedModel, setOllamaSelectedModel] = useState("");
  const [ollamaDeployResult, setOllamaDeployResult] = useState<{ stage: string; message: string } | null>(null);
  const [ollamaError, setOllamaError] = useState<string | null>(null);

  // Provider form
  const [formName, setFormName] = useState("");
  const [formKind, setFormKind] = useState("openai");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formModel, setFormModel] = useState("");
  const [formActive, setFormActive] = useState(true);
  const [formMaxInputTokens, setFormMaxInputTokens] = useState<string>("");
  const [formMaxOutputTokens, setFormMaxOutputTokens] = useState<string>("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showPresets, setShowPresets] = useState(false);

  const smtpConfigured = !!(smtpConfig.host && smtpConfig.host.trim());

  const fetchProviders = useCallback(async () => {
    try {
      const res = await authFetch("/api/providers");
      if (res.ok) setProviders(await res.json());
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  const fetchInviteKeys = useCallback(async () => {
    try {
      const res = await authFetch("/api/invites");
      if (res.ok) setInviteKeys(await res.json());
    } catch { /* silent */ }
  }, []);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) {
        const list = await res.json();
        setCameras(list.map((c: Camera) => ({ id: c.id, name: c.name })));
      }
    } catch { /* silent */ }
  }, []);

  const fetchStorage = useCallback(async () => {
    try {
      const res = await authFetch("/api/storage");
      if (res.ok) {
        setStorage(await res.json());
      } else {
        console.warn("Storage API returned", res.status);
      }
    } catch (err) { console.warn("Storage fetch failed", err); }
    finally { setStorageLoading(false); }
  }, []);

  const fetchSmtp = useCallback(async () => {
    try {
      const res = await authFetch("/api/smtp");
      if (res.ok) {
        const data = await res.json();
        setSmtpConfig({
          host: data.smtp_host || "",
          port: String(data.smtp_port || 587),
          user: data.smtp_user || "",
          password: data.smtp_password || "",
          from: data.smtp_from || "",
          tls: data.smtp_tls ?? true,
        });
      }
    } catch { /* silent */ }
    finally { setSmtpLoading(false); }
  }, []);

  const checkOllama = useCallback(async () => {
    setOllamaChecking(true);
    setOllamaError(null);
    try {
      const res = await authFetch("/api/ollama/status");
      if (res.ok) {
        const data = await res.json();
        setOllamaStatus(data);
        if (data.recommended_model && !ollamaSelectedModel) {
          setOllamaSelectedModel(data.recommended_model);
        }
      } else if (res.status === 401 || res.status === 403) {
        setOllamaError("Admin access required to deploy local AI.");
      } else {
        let detail = "";
        try { detail = (await res.json())?.detail || ""; } catch { /* ignore */ }
        setOllamaError(`Status check failed (${res.status}). ${detail}`.trim());
      }
    } catch (exc) {
      setOllamaError(`Could not reach API. ${exc instanceof Error ? exc.message : "Network error"}`);
    }
    finally { setOllamaChecking(false); }
  }, [authFetch, ollamaSelectedModel]);

  const deployOllama = async () => {
    if (!ollamaSelectedModel) return;
    setOllamaDeploying(true);
    setOllamaDeployResult(null);
    try {
      const res = await authFetch("/api/ollama/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: ollamaSelectedModel }),
      });
      if (res.ok) {
        const data = await res.json();
        setOllamaDeployResult(data);
        if (data.stage === "done") fetchProviders();
      }
    } catch {
      setOllamaDeployResult({ stage: "error", message: "Network error" });
    } finally {
      setOllamaDeploying(false);
    }
  };

  const fetchBlurPersons = useCallback(async () => {
    setBlurLoading(true);
    try {
      const res = await authFetch("/api/persons");
      if (res.ok) setBlurPersons(await res.json());
    } catch { /* silent */ }
    finally { setBlurLoading(false); }
  }, [authFetch]);

  const togglePersonBlur = useCallback(async (personId: string, next: boolean) => {
    setBlurSavingId(personId);
    try {
      const res = await authFetch(`/api/persons/${personId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ privacy_blur: next }),
      });
      if (res.ok) {
        setBlurPersons((prev) => prev.map((p) => p.id === personId ? { ...p, privacy_blur: next } : p));
      }
    } catch { /* silent */ }
    finally { setBlurSavingId(null); }
  }, [authFetch]);

  const fetchAppSettings = useCallback(async () => {
    setNudityLoading(true);
    try {
      const res = await authFetch("/api/system/settings");
      if (res.ok) {
        const data = await res.json();
        if (typeof data.nudity_blur === "boolean") setNudityBlur(data.nudity_blur);
        if (typeof data.nudity_blur_min_score === "number") setNudityMinScore(data.nudity_blur_min_score);
        if (typeof data.journey_idle_seconds === "number") setJourneyIdleSeconds(data.journey_idle_seconds);
        if (typeof data.daily_digest_enabled === "boolean") setDailyDigestEnabled(data.daily_digest_enabled);
        if (typeof data.daily_digest_hour === "number") setDailyDigestHour(data.daily_digest_hour);
        if (typeof data.daily_digest_provider_id === "string") setDailyDigestProviderId(data.daily_digest_provider_id);
      }
    } catch { /* silent */ }
    finally { setNudityLoading(false); }
  }, [authFetch]);

  const saveExtra = useCallback(async (patch: Record<string, unknown>) => {
    setExtraSaving(true);
    try {
      await authFetch("/api/system/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
    } catch { /* silent */ }
    finally { setExtraSaving(false); }
  }, [authFetch]);

  const saveNudityBlur = useCallback(async (next: boolean, score?: number) => {
    setNuditySaving(true);
    try {
      const body: Record<string, unknown> = { nudity_blur: next };
      if (typeof score === "number") body.nudity_blur_min_score = score;
      const res = await authFetch("/api/system/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setNudityBlur(next);
        if (typeof score === "number") setNudityMinScore(score);
      }
    } catch { /* silent */ }
    finally { setNuditySaving(false); }
  }, [authFetch]);

  useEffect(() => {
    fetchProviders();
    fetchInviteKeys();
    fetchCameras();
    fetchStorage();
    fetchSmtp();
    fetchBlurPersons();
    fetchAppSettings();
    checkOllama();
  }, [fetchProviders, fetchInviteKeys, fetchCameras, fetchStorage, fetchSmtp, fetchBlurPersons, fetchAppSettings, checkOllama]);

  // Poll for Ollama installation every 5s while not installed
  useEffect(() => {
    if (ollamaStatus?.installed) return;
    const interval = setInterval(checkOllama, 5000);
    return () => clearInterval(interval);
  }, [ollamaStatus?.installed, checkOllama]);

  const resetForm = () => {
    setFormName(""); setFormKind("openai"); setFormBaseUrl(""); setFormApiKey("");
    setFormModel(""); setFormActive(true); setFormError(""); setShowPresets(false);
    setFormMaxInputTokens(""); setFormMaxOutputTokens("");
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
    } else {
      setShowPresets(true);
    }
    setShowProviderModal(true);
  };

  const openEdit = (p: Provider) => {
    setEditProvider(p);
    setFormName(p.name);
    setFormKind(p.kind);
    setFormBaseUrl(p.base_url);
    setFormApiKey("");
    setFormModel(p.default_model || "");
    setFormActive(p.active);
    setFormMaxInputTokens(p.max_input_tokens != null ? String(p.max_input_tokens) : "");
    setFormMaxOutputTokens(p.max_output_tokens != null ? String(p.max_output_tokens) : "");
    setFormError("");
    setShowPresets(false);
    setShowProviderModal(true);
  };

  const handleKindChange = (kind: string) => {
    setFormKind(kind);
    if (!editProvider) {
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
    if (!formName.trim()) { setFormError("Name is required"); return; }
    if (!formBaseUrl.trim()) { setFormError("Base URL is required"); return; }
    const isLocal = formKind === "ollama" || formBaseUrl.includes("localhost") || formBaseUrl.includes("127.0.0.1");
    if (!isLocal && !formApiKey.trim() && !editProvider) {
      setFormError("API key is required for cloud providers"); return;
    }

    setSubmitting(true);
    setFormError("");

    const parseCap = (raw: string): number | null => {
      const v = raw.trim();
      if (!v) return null;
      const n = Number(v);
      return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
    };
    const body: Record<string, unknown> = {
      name: formName.trim(), kind: formKind,
      base_url: formBaseUrl.trim().replace(/\/+$/, ""),
      default_model: formModel.trim() || null, active: formActive,
      max_input_tokens: parseCap(formMaxInputTokens),
      max_output_tokens: parseCap(formMaxOutputTokens),
    };
    if (formApiKey.trim()) body.api_key = formApiKey.trim();

    try {
      let res: Response;
      if (editProvider) {
        res = await authFetch(`/api/providers/${editProvider.id}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        res = await authFetch("/api/providers", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setFormError(data.detail || "Failed to save provider"); return;
      }
      setShowProviderModal(false);
      fetchProviders();
    } catch { setFormError("Network error"); }
    finally { setSubmitting(false); }
  };

  const handleDelete = async (id: string) => {
    try { await authFetch(`/api/providers/${id}`, { method: "DELETE" }); fetchProviders(); }
    catch { /* silent */ }
  };

  const handleTest = async (provider: Provider) => {
    setTestingId(provider.id);
    setTestResult((prev) => ({ ...prev, [provider.id]: undefined as never }));
    try {
      const res = await authFetch(`/api/providers/${provider.id}/test`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setTestResult((prev) => ({ ...prev, [provider.id]: data }));
      } else {
        setTestResult((prev) => ({ ...prev, [provider.id]: { ok: false, message: `Test returned ${res.status}` } }));
      }
    } catch {
      setTestResult((prev) => ({ ...prev, [provider.id]: { ok: false, message: "Network error" } }));
    } finally { setTestingId(null); }
  };

  const handleCreateInvite = async () => {
    setInviteCreating(true);
    try {
      const body: Record<string, unknown> = { role: inviteRole, max_uses: inviteMaxUses };
      if (inviteCameraIds.length > 0) body.camera_ids = inviteCameraIds;
      const res = await authFetch("/api/invites", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setShowInviteModal(false);
        setInviteRole("viewer"); setInviteCameraIds([]); setInviteMaxUses(1);
        fetchInviteKeys();
      }
    } catch { /* silent */ }
    finally { setInviteCreating(false); }
  };

  const handleDeleteInvite = async (id: string) => {
    try { await authFetch(`/api/invites/${id}`, { method: "DELETE" }); fetchInviteKeys(); }
    catch { /* silent */ }
  };

  const handleSmtpTest = async () => {
    if (!smtpTestEmail.trim()) return;
    setSmtpTesting(true);
    setSmtpTestResult(null);
    try {
      const res = await authFetch("/api/smtp-test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: smtpTestEmail }),
      });
      if (res.ok) setSmtpTestResult(await res.json());
      else setSmtpTestResult({ ok: false, message: `Test returned ${res.status}` });
    } catch { setSmtpTestResult({ ok: false, message: "Network error" }); }
    finally { setSmtpTesting(false); }
  };

  const activeProvider = providers.find((p) => p.active);
  const maxRecordingBytes = storage
    ? storage.cameras.reduce((max, c) => Math.max(max, c.recording_bytes), 1)
    : 1;

  return (
    <div className="px-6 py-6 max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight mb-6">Settings</h1>

      {/* ─── Status Cards ─── */}
      <div className="space-y-3">

        {/* AI Providers card */}
        <div className="rounded-lg border border-border bg-card">
          <button
            onClick={() => setShowProviders(!showProviders)}
            className="w-full px-4 py-3.5 flex items-center justify-between text-left"
          >
            <div className="flex items-center gap-3">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${activeProvider ? "bg-green-500" : "bg-yellow-500"}`} />
              <div>
                <div className="text-sm font-medium">AI Providers</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  {loading ? "Loading." : activeProvider
                    ? `${activeProvider.name} (${activeProvider.default_model || "default"})`
                    : "No provider configured"}
                </div>
              </div>
            </div>
            <svg
              width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              className={`text-muted-foreground transition-transform ${showProviders ? "rotate-180" : ""}`}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>

          {showProviders && (
            <div className="px-4 pb-4 border-t border-border pt-3 space-y-3">
              {/* Configured providers */}
              {providers.length > 0 && (
                <div className="space-y-2">
                  {providers.map((p) => (
                    <div key={p.id} className="rounded-md border border-border bg-background p-3">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-2.5">
                          <span className={`w-1.5 h-1.5 rounded-full ${p.active ? "bg-green-500" : "bg-muted-foreground/40"}`} />
                          <div>
                            <div className="font-medium text-sm">{p.name}</div>
                            <div className="text-[11px] text-muted-foreground">
                              {p.kind} {p.default_model ? ` / ${p.default_model}` : ""}
                            </div>
                          </div>
                        </div>
                        <div className="flex gap-1">
                          <button onClick={() => handleTest(p)} disabled={testingId === p.id}
                            className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors disabled:opacity-50">
                            {testingId === p.id ? "Testing." : "Test"}
                          </button>
                          <button onClick={() => openEdit(p)}
                            className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors">
                            Edit
                          </button>
                          <button onClick={() => handleDelete(p.id)}
                            className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors">
                            Del
                          </button>
                        </div>
                      </div>
                      {testResult[p.id] && (
                        <div className={`mt-2 text-xs px-2 py-2 rounded space-y-1 ${testResult[p.id].ok ? "bg-green-900/20 text-green-400" : "bg-red-900/20 text-red-400"}`}>
                          <div className="flex items-center justify-between">
                            <span>{testResult[p.id].message}</span>
                            {testResult[p.id].latency_ms != null && (
                              <span className="font-mono text-[10px] opacity-70">{testResult[p.id].latency_ms}ms</span>
                            )}
                          </div>
                          {testResult[p.id].models && testResult[p.id].models!.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {testResult[p.id].models!.slice(0, 8).map((m) => (
                                <span key={m} className="px-1 py-0.5 text-[10px] rounded bg-black/20 font-mono">{m}</span>
                              ))}
                              {testResult[p.id].models!.length > 8 && (
                                <span className="text-[10px] opacity-70">+{testResult[p.id].models!.length - 8} more</span>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <button onClick={() => openCreate()}
                className="w-full px-3 py-2 text-sm rounded-md border border-dashed border-border hover:border-accent/50 hover:bg-accent/5 transition-colors text-muted-foreground hover:text-foreground">
                + Add cloud provider
              </button>
            </div>
          )}
        </div>

        {/* One-Click Local AI (Ollama) */}
        {!providers.some((p) => p.kind === "ollama") && (
          <div className="rounded-lg border border-accent/30 bg-accent/5 p-4">
            <div className="flex items-start gap-3">
              <div className="w-9 h-9 rounded-lg bg-accent/15 flex items-center justify-center flex-shrink-0 mt-0.5">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-accent">
                  <path d="M12 2a4 4 0 0 1 4 4v1a2 2 0 0 1 2 2v1a4 4 0 0 1-2 3.46V16a6 6 0 0 1-12 0v-2.54A4 4 0 0 1 2 10V9a2 2 0 0 1 2-2V6a4 4 0 0 1 4-4" />
                  <circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <h2 className="text-sm font-semibold mb-1">Deploy Local AI</h2>
                <p className="text-xs text-muted-foreground leading-relaxed mb-3">
                  Run a vision model locally using Ollama. No cloud, no API keys, no data leaves your network.
                </p>

                {ollamaChecking ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="w-3.5 h-3.5 border-2 border-muted-foreground/30 border-t-muted-foreground rounded-full animate-spin" />
                    Checking system.
                  </div>
                ) : ollamaStatus ? (
                  <div className="space-y-3">
                    {/* Status badges */}
                    <div className="flex flex-wrap gap-2 text-[11px]">
                      <span className={`px-2 py-0.5 rounded-full border ${ollamaStatus.installed ? "border-green-800/40 bg-green-900/20 text-green-400" : "border-red-800/40 bg-red-900/20 text-red-400"}`}>
                        Ollama {ollamaStatus.installed ? "installed" : "not installed"}
                      </span>
                      {ollamaStatus.installed && (
                        <span className={`px-2 py-0.5 rounded-full border ${ollamaStatus.running ? "border-green-800/40 bg-green-900/20 text-green-400" : "border-yellow-800/40 bg-yellow-900/20 text-yellow-400"}`}>
                          {ollamaStatus.running ? "Running" : "Not running (will auto-start)"}
                        </span>
                      )}
                      {ollamaStatus.system_ram_gb && (
                        <span className="px-2 py-0.5 rounded-full border border-border bg-muted/30 text-muted-foreground">
                          {ollamaStatus.system_ram_gb} GB RAM
                        </span>
                      )}
                    </div>

                    {!ollamaStatus.installed ? (
                      <div className="rounded-md border border-border bg-card p-3">
                        <p className="text-xs text-muted-foreground mb-2">
                          Install Ollama first. Takes about a minute. This page detects it automatically.
                        </p>
                        <div className="flex items-center gap-3">
                          <a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-accent text-black font-medium hover:bg-accent/90 transition-colors">
                            Download Ollama
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                              <path d="M7 17L17 7" /><path d="M7 7h10v10" />
                            </svg>
                          </a>
                          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                            <span className="w-2 h-2 border border-muted-foreground/40 border-t-muted-foreground rounded-full animate-spin" />
                            Waiting for installation.
                          </span>
                        </div>
                      </div>
                    ) : (
                      <>
                        {/* Model selector */}
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block mb-2">Choose a model</span>
                          <div className="space-y-2">
                            {(() => {
                              const families = new Map<string, typeof ollamaStatus.available_models>();
                              for (const m of ollamaStatus.available_models) {
                                const family = (m as Record<string, unknown>).family as string || "Other";
                                if (!families.has(family)) families.set(family, []);
                                families.get(family)!.push(m);
                              }
                              return Array.from(families.entries()).map(([family, models]) => (
                                <div key={family}>
                                  <div className="text-[10px] font-medium text-muted-foreground mb-1">{family}</div>
                                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                    {models.map((m) => {
                                      const fits = ollamaStatus.system_ram_gb ? ollamaStatus.system_ram_gb >= m.ram_gb * 1.3 : true;
                                      const isInstalled = ollamaStatus.models.some((im) => im.startsWith(m.name.split(":")[0]));
                                      return (
                                        <button key={m.name}
                                          onClick={() => setOllamaSelectedModel(m.name)}
                                          disabled={!fits}
                                          className={`relative text-left px-3 py-2 rounded-lg border transition-colors ${
                                            ollamaSelectedModel === m.name
                                              ? "border-accent/50 bg-accent/10"
                                              : fits ? "border-border hover:border-muted-foreground/30" : "border-border opacity-40 cursor-not-allowed"
                                          }`}>
                                          <div className="flex items-center justify-between">
                                            <span className="text-xs font-medium">{m.label}</span>
                                            <div className="flex gap-1">
                                              {isInstalled && (
                                                <span className="text-[9px] px-1 py-0.5 rounded bg-green-900/30 text-green-400 border border-green-800/40">ready</span>
                                              )}
                                              {m.name === ollamaStatus.recommended_model && (
                                                <span className="text-[9px] px-1 py-0.5 rounded bg-accent/20 text-accent border border-accent/30">recommended</span>
                                              )}
                                              {!fits && (
                                                <span className="text-[9px] px-1 py-0.5 rounded bg-red-900/30 text-red-400 border border-red-800/40">needs {m.ram_gb}+ GB</span>
                                              )}
                                            </div>
                                          </div>
                                          <div className="text-[10px] text-muted-foreground mt-0.5">{m.description}</div>
                                        </button>
                                      );
                                    })}
                                  </div>
                                </div>
                              ));
                            })()}

                            {/* Custom model input */}
                            <div>
                              <div className="text-[10px] font-medium text-muted-foreground mb-1">Custom</div>
                              <input type="text"
                                placeholder="Any Ollama model, e.g. llava-phi3"
                                value={ollamaStatus.available_models.some((m) => m.name === ollamaSelectedModel) ? "" : ollamaSelectedModel}
                                onChange={(e) => setOllamaSelectedModel(e.target.value.trim())}
                                className="w-full px-3 py-2 rounded-lg bg-background border border-border text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                              />
                              <div className="text-[10px] text-muted-foreground mt-1">
                                Browse all models at <a href="https://ollama.com/search?c=vision" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">ollama.com/search</a>
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Deploy button */}
                        <div className="flex items-center gap-3">
                          <button onClick={deployOllama}
                            disabled={ollamaDeploying || !ollamaSelectedModel}
                            className="px-4 py-2 text-xs font-medium rounded-lg bg-accent text-black hover:bg-accent/90 disabled:opacity-50 transition-colors">
                            {ollamaDeploying ? (
                              <span className="flex items-center gap-2">
                                <span className="w-3 h-3 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                                Deploying. This may take a few minutes.
                              </span>
                            ) : `Deploy ${ollamaSelectedModel}`}
                          </button>
                          {ollamaDeployResult && (
                            <span className={`text-xs ${ollamaDeployResult.stage === "done" ? "text-green-400" : ollamaDeployResult.stage === "error" ? "text-red-400" : "text-muted-foreground"}`}>
                              {ollamaDeployResult.message}
                            </span>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <button onClick={checkOllama} className="text-xs text-accent hover:text-accent/80 transition-colors">
                      Check system compatibility
                    </button>
                    {ollamaError && (
                      <p className="text-[11px] text-red-400">{ollamaError}</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Storage card */}
        <div className="rounded-lg border border-border bg-card">
          <button
            onClick={() => setShowStorage(!showStorage)}
            className="w-full px-4 py-3.5 flex items-center justify-between text-left"
          >
            <div className="flex items-center gap-3">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                storageLoading ? "bg-muted-foreground/40" : storage ? "bg-green-500" : "bg-muted-foreground/40"
              }`} />
              <div>
                <div className="text-sm font-medium">Storage</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  {storageLoading ? "Loading." : storage
                    ? storage.cameras.length > 0
                      ? `${formatBytes(storage.total_recording_bytes)} used across ${storage.cameras.length} camera${storage.cameras.length !== 1 ? "s" : ""}`
                      : "No cameras configured yet"
                    : "Could not connect to storage API"}
                </div>
              </div>
            </div>
            <svg
              width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              className={`text-muted-foreground transition-transform ${showStorage ? "rotate-180" : ""}`}
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>

          {showStorage && (
            <div className="px-4 pb-4 border-t border-border pt-3">
              {storage ? (
                <div className="space-y-2">
                  {storage.cameras.length === 0 ? (
                    <div className="text-sm text-muted-foreground py-3 text-center">No cameras configured yet.</div>
                  ) : storage.cameras.map((cam) => {
                    const pct = usagePercent(cam);
                    const barWidth = cam.recording_bytes > 0 ? Math.max((cam.recording_bytes / maxRecordingBytes) * 100, 2) : 0;
                    return (
                      <div key={cam.camera_id} className="rounded-md border border-border bg-background p-3">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-sm">{cam.camera_name}</span>
                            <span className="text-xs text-muted-foreground">{formatBytes(cam.recording_bytes)}</span>
                          </div>
                          <span className="text-[11px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{retentionLabel(cam)}</span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden mb-2">
                          <div className={`h-full rounded-full transition-all ${barColor(pct)}`} style={{ width: `${barWidth}%` }} />
                        </div>
                        <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
                          <span>{cam.recording_count} recording{cam.recording_count !== 1 ? "s" : ""}</span>
                          <span>{cam.observation_count} observation{cam.observation_count !== 1 ? "s" : ""}</span>
                          {pct !== null && (
                            <span className={pct >= 80 ? "text-red-400" : pct >= 50 ? "text-yellow-400" : "text-green-400"}>
                              {pct.toFixed(0)}% of limit
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground py-3 text-center">Could not connect to storage API. Check that the server is running.</div>
              )}
            </div>
          )}
        </div>

        {/* Email card */}
        <div className="rounded-lg border border-border bg-card px-4 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
              smtpLoading ? "bg-muted-foreground/40" : smtpConfigured ? "bg-green-500" : "bg-muted-foreground/40"
            }`} />
            <div>
              <div className="text-sm font-medium">Email</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {smtpLoading ? "Loading." : smtpConfigured
                  ? `Configured (${smtpConfig.host})`
                  : "SMTP is not configured"}
              </div>
            </div>
          </div>
          <button onClick={() => setShowSmtpModal(true)}
            className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted transition-colors">
            {smtpConfigured ? "View" : "Configure"}
          </button>
        </div>

        {/* Invite Keys card */}
        <div className="rounded-lg border border-border bg-card px-4 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${inviteKeys.length > 0 ? "bg-green-500" : "bg-muted-foreground/40"}`} />
            <div>
              <div className="text-sm font-medium">Invite Keys</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {inviteKeys.length > 0
                  ? `${inviteKeys.length} active key${inviteKeys.length !== 1 ? "s" : ""}`
                  : "No invite keys created"}
              </div>
            </div>
          </div>
          <button onClick={() => setShowInviteModal(true)}
            className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted transition-colors">
            Manage
          </button>
        </div>

        {/* Privacy Blur card */}
        {(() => {
          const enabledCount = blurPersons.filter((p) => p.privacy_blur).length;
          return (
            <div className="rounded-lg border border-border bg-card px-4 py-3.5 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${enabledCount > 0 ? "bg-amber-500" : "bg-muted-foreground/40"}`} />
                <div>
                  <div className="text-sm font-medium flex items-center gap-2">
                    Privacy Blur
                    <span className="text-[10px] font-normal uppercase tracking-wider text-amber-500/80 bg-amber-500/10 border border-amber-500/30 rounded px-1 py-0.5">safety</span>
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {enabledCount > 0
                      ? `Blurring ${enabledCount} protected ${enabledCount === 1 ? "person" : "people"} on all recordings.`
                      : "Hide specific faces and bodies from saved footage."}
                  </div>
                </div>
              </div>
              <button onClick={() => setShowBlurModal(true)}
                className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted transition-colors">
                Manage
              </button>
            </div>
          );
        })()}

        {/* Nudity Blur card */}
        <div className="rounded-lg border border-border bg-card px-4 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${nudityBlur ? "bg-amber-500" : "bg-muted-foreground/40"}`} />
            <div>
              <div className="text-sm font-medium flex items-center gap-2">
                Nudity Blur
                <span className="text-[10px] font-normal uppercase tracking-wider text-amber-500/80 bg-amber-500/10 border border-amber-500/30 rounded px-1 py-0.5">safety</span>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {nudityLoading
                  ? "Loading."
                  : nudityBlur
                    ? `Automatically blur exposed body parts in every recording. Min score ${nudityMinScore.toFixed(2)}.`
                    : "Disabled. Recordings will not be scanned for nudity."}
              </div>
            </div>
          </div>
          <button
            disabled={nudityLoading || nuditySaving}
            onClick={() => saveNudityBlur(!nudityBlur)}
            aria-label={nudityBlur ? "Disable nudity blur" : "Enable nudity blur"}
            className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${nudityBlur ? "bg-amber-500" : "bg-muted"} ${nuditySaving ? "opacity-50" : ""}`}
          >
            <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${nudityBlur ? "left-[1.375rem]" : "left-0.5"}`} />
          </button>
        </div>

        {/* Cross-camera journey idle */}
        <div className="rounded-lg border border-border bg-card px-4 py-3.5">
          <div className="text-sm font-medium mb-2">Journey idle window</div>
          <p className="text-xs text-muted-foreground mb-3">
            How long a subject can stay off-camera before a cross-camera
            journey closes. Bigger properties want a longer window so a
            walk between cameras doesn&apos;t end the trip prematurely.
          </p>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={60}
              max={1800}
              step={30}
              value={journeyIdleSeconds}
              onChange={(e) => setJourneyIdleSeconds(Number(e.target.value))}
              onMouseUp={() => saveExtra({ journey_idle_seconds: journeyIdleSeconds })}
              onTouchEnd={() => saveExtra({ journey_idle_seconds: journeyIdleSeconds })}
              className="flex-1 accent-accent"
            />
            <span className="font-mono text-xs text-muted-foreground w-16 text-right">
              {Math.round(journeyIdleSeconds / 60)} min
            </span>
          </div>
        </div>

        {/* Daily digest */}
        <div className="rounded-lg border border-border bg-card px-4 py-3.5 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium flex items-center gap-2">
                Morning digest
                <span className="text-[10px] font-normal uppercase tracking-wider text-amber-500/80 bg-amber-500/10 border border-amber-500/30 rounded px-1 py-0.5">household</span>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {dailyDigestEnabled
                  ? `Bullet-point recap of the last 24h, generated at ${String(dailyDigestHour).padStart(2, "0")}:00 local time.`
                  : "Disabled. No daily digest will be generated automatically."}
              </div>
            </div>
            <button
              disabled={extraSaving}
              onClick={() => {
                const next = !dailyDigestEnabled;
                setDailyDigestEnabled(next);
                saveExtra({ daily_digest_enabled: next });
              }}
              className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${dailyDigestEnabled ? "bg-amber-500" : "bg-muted"}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${dailyDigestEnabled ? "left-[1.375rem]" : "left-0.5"}`} />
            </button>
          </div>
          {dailyDigestEnabled && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[11px] text-muted-foreground block mb-1">
                  Hour of day (local)
                </label>
                <select
                  value={dailyDigestHour}
                  onChange={(e) => {
                    const h = Number(e.target.value);
                    setDailyDigestHour(h);
                    saveExtra({ daily_digest_hour: h });
                  }}
                  className="w-full px-2 py-1.5 text-xs rounded border border-border bg-background"
                >
                  {Array.from({ length: 24 }).map((_, h) => (
                    <option key={h} value={h}>
                      {String(h).padStart(2, "0")}:00
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground block mb-1">
                  Provider override
                </label>
                <select
                  value={dailyDigestProviderId}
                  onChange={(e) => {
                    const v = e.target.value;
                    setDailyDigestProviderId(v);
                    saveExtra({ daily_digest_provider_id: v || null });
                  }}
                  className="w-full px-2 py-1.5 text-xs rounded border border-border bg-background"
                >
                  <option value="">(system default)</option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ─── Modals ─── */}

      {/* Privacy Blur Modal */}
      {showBlurModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowBlurModal(false)} />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-start justify-between mb-1">
              <h2 className="text-lg font-semibold">Privacy Blur</h2>
              <button onClick={() => setShowBlurModal(false)} className="text-muted-foreground hover:text-foreground text-lg leading-none">×</button>
            </div>
            <p className="text-xs text-muted-foreground mb-4 leading-relaxed">
              Toggle a person on to blur their face and upper body in every recording that includes them. Runs after each clip finishes. Detection uses the same face model as alerts, so the person needs at least one reference photo on file.
            </p>

            {blurLoading ? (
              <div className="text-xs text-muted-foreground py-6 text-center">Loading people.</div>
            ) : blurPersons.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-6 text-center">
                <div className="text-sm font-medium mb-1">No people yet</div>
                <div className="text-xs text-muted-foreground">Add someone on the People page and upload a reference photo, then come back here to protect them.</div>
              </div>
            ) : (
              <div className="space-y-1.5">
                {blurPersons.map((p) => (
                  <div key={p.id} className="flex items-center gap-3 rounded-md border border-border bg-background px-3 py-2">
                    <div className="w-9 h-9 rounded-full overflow-hidden bg-muted flex-shrink-0">
                      {p.photo_path ? (
                        <img src={`/api/persons/${p.id}/photo${token ? `?token=${token}` : ""}`} alt={p.display_name} className="w-full h-full object-cover" />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-xs font-semibold text-muted-foreground">
                          {p.display_name.charAt(0).toUpperCase()}
                        </div>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{p.display_name}</div>
                      <div className="text-[11px] text-muted-foreground truncate">{p.relationship || "no relationship set"}</div>
                    </div>
                    <button
                      onClick={() => togglePersonBlur(p.id, !p.privacy_blur)}
                      disabled={blurSavingId === p.id}
                      className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${p.privacy_blur ? "bg-amber-500" : "bg-muted"} ${blurSavingId === p.id ? "opacity-50" : ""}`}
                      title={p.privacy_blur ? "Blur enabled" : "Blur disabled"}
                    >
                      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${p.privacy_blur ? "left-[1.375rem]" : "left-0.5"}`} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-5 pt-4 border-t border-border text-[11px] text-muted-foreground leading-relaxed">
              <strong className="text-foreground">How it works.</strong> After a recording finishes, Nurby scans sampled frames for protected faces. Any match triggers a heavy Gaussian blur over the head and upper torso for the full window around that frame. The original unblurred clip is replaced, not kept alongside.
            </div>

            <div className="mt-4 flex justify-end">
              <button onClick={() => setShowBlurModal(false)}
                className="px-3 py-1.5 text-xs rounded-md bg-foreground text-background font-medium hover:opacity-90">
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add/Edit Provider Modal */}
      {showProviderModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowProviderModal(false)} />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
            <h2 className="text-lg font-semibold mb-4">
              {editProvider ? "Edit provider" : "Add AI provider"}
            </h2>

            {/* Presets (only in create mode) */}
            {!editProvider && showPresets && (
              <div className="mb-4">
                <div className="text-xs font-medium text-muted-foreground mb-2">Quick setup</div>
                <div className="grid grid-cols-2 gap-1.5 mb-3">
                  {ALL_PROVIDERS.map((preset) => (
                    <button key={preset.name}
                      onClick={() => {
                        setFormKind(preset.kind);
                        setFormName(preset.name);
                        setFormBaseUrl(preset.url);
                        setFormModel(preset.model);
                        setShowPresets(false);
                      }}
                      className="rounded-md border border-border bg-background p-2.5 text-left hover:border-accent/50 transition-colors group">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="font-medium text-xs group-hover:text-accent transition-colors">{preset.name}</span>
                        {!preset.needsKey && (
                          <span className="text-[9px] px-1 py-0.5 rounded bg-green-900/30 text-green-400 border border-green-800/40">local</span>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground leading-snug">{preset.description}</div>
                    </button>
                  ))}
                </div>
                <div className="relative">
                  <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-border" /></div>
                  <div className="relative flex justify-center">
                    <span className="bg-card px-2 text-[10px] text-muted-foreground">or configure manually</span>
                  </div>
                </div>
              </div>
            )}

            <div className="space-y-3">
              {/* Kind */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Provider type</label>
                <div className="grid grid-cols-4 gap-1">
                  {PROVIDER_KINDS.map((pk) => (
                    <button key={pk.value} onClick={() => handleKindChange(pk.value)}
                      className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                        formKind === pk.value ? "border-accent bg-accent/10 text-accent" : "border-border hover:bg-muted"
                      }`}>
                      {pk.label}
                    </button>
                  ))}
                </div>
              </div>

              {formKind === "openai" && (
                <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                  OpenAI-compatible API. Works with OpenAI, Together, Groq, Fireworks, Mistral, DeepSeek, LMStudio, vLLM, and others.
                </div>
              )}

              {/* Name */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Display name</label>
                <input type="text" value={formName} onChange={(e) => setFormName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                  placeholder="My OpenAI" />
              </div>

              {/* Base URL */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Base URL</label>
                <input type="url" value={formBaseUrl} onChange={(e) => setFormBaseUrl(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                  placeholder="https://api.openai.com" />
              </div>

              {/* API Key */}
              {!(formKind === "ollama" || formBaseUrl.includes("localhost") || formBaseUrl.includes("127.0.0.1")) && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">API key</label>
                  <input type="password" value={formApiKey} onChange={(e) => setFormApiKey(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                    placeholder={editProvider ? "Leave blank to keep existing key" : "sk-..."} />
                </div>
              )}

              {/* Model */}
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Default model</label>
                <input type="text" value={formModel} onChange={(e) => setFormModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                  placeholder={formKind === "openai" ? "gpt-4o-mini" : formKind === "anthropic" ? "claude-sonnet-4-20250514" : "moondream"} />
              </div>

              {/* Token caps */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">
                    Max input tokens
                  </label>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={64}
                    value={formMaxInputTokens}
                    onChange={(e) => setFormMaxInputTokens(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                    placeholder="unlimited"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Cap on the prompt size we send. Empty means use the model default.
                  </p>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">
                    Max output tokens
                  </label>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={16}
                    value={formMaxOutputTokens}
                    onChange={(e) => setFormMaxOutputTokens(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono focus:outline-none focus:border-accent"
                    placeholder="unlimited"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Hard ceiling on the response. Per-camera caps further tighten this.
                  </p>
                </div>
              </div>

              {/* Active */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={formActive} onChange={(e) => setFormActive(e.target.checked)} className="accent-green-500" />
                <span className="text-sm">Active (used for VLM calls)</span>
              </label>

              {formError && <div className="text-xs text-red-400">{formError}</div>}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowProviderModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">Cancel</button>
              <button onClick={handleSubmit} disabled={submitting}
                className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
                {submitting ? "Saving." : editProvider ? "Save" : "Add provider"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SMTP Modal */}
      {showSmtpModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowSmtpModal(false)} />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold mb-1">Email Configuration</h2>
            <p className="text-xs text-muted-foreground mb-4">
              SMTP settings are configured via environment variables in your .env file.
            </p>

            {smtpLoading ? (
              <div className="text-sm text-muted-foreground py-6 text-center">Loading.</div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">SMTP Host</label>
                    <input type="text" value={smtpConfig.host} disabled
                      className="w-full px-3 py-2 rounded-md bg-muted border border-border text-sm opacity-70" placeholder="Not set" />
                    <span className="text-[10px] text-muted-foreground">env SMTP_HOST</span>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">SMTP Port</label>
                    <input type="text" value={smtpConfig.port} disabled
                      className="w-full px-3 py-2 rounded-md bg-muted border border-border text-sm opacity-70" placeholder="587" />
                    <span className="text-[10px] text-muted-foreground">env SMTP_PORT</span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">Username</label>
                    <input type="text" value={smtpConfig.user} disabled
                      className="w-full px-3 py-2 rounded-md bg-muted border border-border text-sm opacity-70" placeholder="Not set" />
                    <span className="text-[10px] text-muted-foreground">env SMTP_USER</span>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">Password</label>
                    <input type="password" value={smtpConfig.password} disabled
                      className="w-full px-3 py-2 rounded-md bg-muted border border-border text-sm opacity-70" placeholder="Not set" />
                    <span className="text-[10px] text-muted-foreground">env SMTP_PASSWORD</span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">From Address</label>
                    <input type="text" value={smtpConfig.from} disabled
                      className="w-full px-3 py-2 rounded-md bg-muted border border-border text-sm opacity-70" placeholder="Not set" />
                    <span className="text-[10px] text-muted-foreground">env SMTP_FROM</span>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground block mb-1">TLS</label>
                    <div className="flex items-center gap-2 px-3 py-2">
                      <span className={`w-2 h-2 rounded-full ${smtpConfig.tls ? "bg-green-500" : "bg-yellow-500"}`} />
                      <span className="text-sm">{smtpConfig.tls ? "Enabled" : "Disabled"}</span>
                    </div>
                    <span className="text-[10px] text-muted-foreground">env SMTP_TLS</span>
                  </div>
                </div>

                {/* Test */}
                <div className="pt-3 border-t border-border">
                  <label className="text-xs font-medium text-muted-foreground block mb-1">Send test email</label>
                  <div className="flex gap-2">
                    <input type="email" value={smtpTestEmail} onChange={(e) => setSmtpTestEmail(e.target.value)}
                      className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
                      placeholder="test@example.com" />
                    <button onClick={handleSmtpTest} disabled={smtpTesting || !smtpTestEmail.trim()}
                      className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors disabled:opacity-50">
                      {smtpTesting ? "Sending." : "Test"}
                    </button>
                  </div>
                  {smtpTestResult && (
                    <div className={`mt-2 text-xs px-2 py-2 rounded ${smtpTestResult.ok ? "bg-green-900/20 text-green-400" : "bg-red-900/20 text-red-400"}`}>
                      {smtpTestResult.message}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="flex justify-end mt-5">
              <button onClick={() => setShowSmtpModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Invite Key Modal */}
      {showInviteModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowInviteModal(false)} />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-md shadow-xl">
            <h2 className="text-lg font-semibold mb-4">Invite Keys</h2>

            {/* Existing keys */}
            {inviteKeys.length > 0 && (
              <div className="space-y-2 mb-4">
                {inviteKeys.map((ik) => (
                  <div key={ik.id} className="rounded-md border border-border bg-background p-3 flex items-center justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                      <code className="text-xs font-mono bg-muted px-2 py-1 rounded select-all truncate">{ik.key}</code>
                      <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                        {ik.role} / {ik.use_count}/{ik.max_uses} uses
                      </span>
                    </div>
                    <button onClick={() => handleDeleteInvite(ik.id)}
                      className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors ml-2 flex-shrink-0">
                      Revoke
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Create new key form */}
            <div className="border-t border-border pt-4 space-y-3">
              <div className="text-xs font-medium text-muted-foreground">Create new key</div>
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Role</label>
                <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent">
                  <option value="viewer">Viewer</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">Max Uses</label>
                <input type="number" min={1} max={100} value={inviteMaxUses}
                  onChange={(e) => setInviteMaxUses(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent" />
              </div>
              {cameras.length > 0 && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground block mb-1">Camera Access (optional)</label>
                  <div className="space-y-1 max-h-32 overflow-y-auto">
                    {cameras.map((cam) => (
                      <label key={cam.id} className="flex items-center gap-2 cursor-pointer text-sm">
                        <input type="checkbox" checked={inviteCameraIds.includes(cam.id)}
                          onChange={(e) => {
                            if (e.target.checked) setInviteCameraIds([...inviteCameraIds, cam.id]);
                            else setInviteCameraIds(inviteCameraIds.filter((id) => id !== cam.id));
                          }} className="accent-accent" />
                        {cam.name}
                      </label>
                    ))}
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-1">Leave empty to grant access to all cameras</p>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowInviteModal(false)}
                className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors">Close</button>
              <button onClick={handleCreateInvite} disabled={inviteCreating}
                className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50">
                {inviteCreating ? "Creating." : "Create key"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
