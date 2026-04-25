"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
// Inline SVG glyphs. The frontend does not bundle lucide-react.
const ArrowLeft = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="19" y1="12" x2="5" y2="12" />
    <polyline points="12 19 5 12 12 5" />
  </svg>
);
const Mic = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
);
const ShieldCheck = ({ className }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <polyline points="9 12 11 14 15 10" />
  </svg>
);

import { useAuth } from "@/lib/auth";

interface AudioConfig {
  audio_capture_enabled: boolean;
  audio_transcribe_enabled: boolean;
  audio_store_raw: boolean;
  transcript_store: string;
  audio_language: string;
  audio_retention_days: number;
  transcript_retention_days: number;
  stt_budget_minutes_per_hour: number;
}

interface Transcript {
  id: string;
  camera_id: string;
  audio_capture_id: string | null;
  started_at: string;
  ended_at: string;
  text: string;
  language: string | null;
  provider: string;
  filtered: boolean;
}

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function CameraAudioPage() {
  const params = useParams();
  const router = useRouter();
  const cameraId = params?.id as string;
  const { token } = useAuth();

  const [config, setConfig] = useState<AudioConfig | null>(null);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !cameraId) return;
    let cancelled = false;
    (async () => {
      try {
        const camResp = await fetch(`${API}/api/cameras/${cameraId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const cam = await camResp.json();
        if (cancelled) return;
        setConfig({
          audio_capture_enabled: !!cam.audio_capture_enabled,
          audio_transcribe_enabled: !!cam.audio_transcribe_enabled,
          audio_store_raw: !!cam.audio_store_raw,
          transcript_store: cam.transcript_store || "full",
          audio_language: cam.audio_language || "en",
          audio_retention_days: cam.audio_retention_days ?? 7,
          transcript_retention_days: cam.transcript_retention_days ?? 30,
          stt_budget_minutes_per_hour: cam.stt_budget_minutes_per_hour ?? 30,
        });

        const txResp = await fetch(
          `${API}/api/transcripts?camera_id=${cameraId}&limit=100`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        const txList = await txResp.json();
        if (!cancelled) setTranscripts(Array.isArray(txList) ? txList : []);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || "Failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cameraId, token]);

  const update = async (patch: Partial<AudioConfig>) => {
    if (!config) return;
    setSaving(true);
    setError(null);
    try {
      const resp = await fetch(`${API}/api/audio/cameras/${cameraId}/audio`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(patch),
      });
      if (!resp.ok) throw new Error(await resp.text());
      const next = await resp.json();
      setConfig({ ...config, ...next });
    } catch (e: any) {
      setError(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (!config) {
    return (
      <div className="min-h-screen bg-black text-zinc-200 p-8">
        <div className="text-zinc-400">Loading audio settings.</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-zinc-200">
      <div className="max-w-5xl mx-auto p-8">
        <div className="flex items-center gap-3 mb-6">
          <Link
            href={`/cameras/${cameraId}`}
            className="text-zinc-400 hover:text-zinc-100"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Mic className="w-5 h-5 text-emerald-400" />
            Audio &amp; transcripts
          </h1>
        </div>

        {error ? (
          <div className="mb-4 rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-5 mb-6">
          <h2 className="text-sm font-medium text-zinc-300 mb-4 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            Privacy switches
          </h2>
          <div className="grid gap-3">
            <ToggleRow
              label="Capture audio"
              hint="Pulls the audio track from this camera. Required before any transcription."
              value={config.audio_capture_enabled}
              onChange={(v) => update({ audio_capture_enabled: v })}
              disabled={saving}
            />
            <ToggleRow
              label="Transcribe speech"
              hint="Runs on-device STT against captured audio."
              value={config.audio_transcribe_enabled}
              onChange={(v) => update({ audio_transcribe_enabled: v })}
              disabled={saving || !config.audio_capture_enabled}
            />
            <ToggleRow
              label="Store raw audio"
              hint="Keeps Opus-encoded clips on disk for playback. Off = transcripts only."
              value={config.audio_store_raw}
              onChange={(v) => update({ audio_store_raw: v })}
              disabled={saving || !config.audio_capture_enabled}
            />
            <SelectRow
              label="Transcript storage"
              value={config.transcript_store}
              options={[
                { v: "full", l: "Full text" },
                { v: "redacted", l: "Redacted" },
                { v: "summary_only", l: "Summary only" },
                { v: "off", l: "Off (live only)" },
              ]}
              onChange={(v) => update({ transcript_store: v })}
              disabled={saving}
            />
          </div>
        </section>

        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-5 mb-6">
          <h2 className="text-sm font-medium text-zinc-300 mb-4">Retention</h2>
          <div className="grid gap-3 grid-cols-2">
            <NumberRow
              label="Audio retention (days)"
              value={config.audio_retention_days}
              onChange={(v) => update({ audio_retention_days: v })}
              disabled={saving}
            />
            <NumberRow
              label="Transcript retention (days)"
              value={config.transcript_retention_days}
              onChange={(v) => update({ transcript_retention_days: v })}
              disabled={saving}
            />
            <NumberRow
              label="STT budget (min/hour)"
              value={config.stt_budget_minutes_per_hour}
              onChange={(v) => update({ stt_budget_minutes_per_hour: v })}
              disabled={saving}
            />
          </div>
        </section>

        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-5">
          <h2 className="text-sm font-medium text-zinc-300 mb-4">
            Recent transcripts ({transcripts.length})
          </h2>
          {transcripts.length === 0 ? (
            <div className="text-sm text-zinc-500">
              Nothing yet. Enable capture and transcription, then speak near the
              camera.
            </div>
          ) : (
            <div className="grid gap-2">
              {transcripts.map((t) => (
                <div
                  key={t.id}
                  className="rounded border border-zinc-800 bg-zinc-900 p-3"
                >
                  <div className="text-xs text-zinc-500 mb-1">
                    {new Date(t.started_at).toLocaleString()} · {t.provider}
                  </div>
                  <div className="text-sm italic text-zinc-100">{t.text}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  value,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-sm text-zinc-100">{label}</div>
        <div className="text-xs text-zinc-500 mt-0.5">{hint}</div>
      </div>
      <button
        type="button"
        onClick={() => onChange(!value)}
        disabled={disabled}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
          value ? "bg-emerald-500" : "bg-zinc-700"
        } ${disabled ? "opacity-40" : ""}`}
      >
        <span
          className={`inline-block h-5 w-5 transform rounded-full bg-white transition ${
            value ? "translate-x-5" : "translate-x-0.5"
          }`}
        />
      </button>
    </div>
  );
}

function SelectRow({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: { v: string; l: string }[];
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="text-sm text-zinc-100">{label}</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
      >
        {options.map((o) => (
          <option key={o.v} value={o.v}>
            {o.l}
          </option>
        ))}
      </select>
    </div>
  );
}

function NumberRow({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-zinc-400">{label}</span>
      <input
        type="number"
        value={value}
        min={0}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm"
      />
    </label>
  );
}
