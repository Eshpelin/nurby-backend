"use client";

// TestPanel lets the user dry-run the current draft rule against
// /api/rules/test and replay an existing rule against the last N hours
// of persisted observations via /api/rules/{id}/replay.

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import type {
  Camera,
  RulePayload,
  RuleReplayResponse,
  RuleTestActionPreview,
  RuleTestResponse,
} from "./types";

const REPLAY_HOURS_OPTIONS = [
  { value: 1, label: "1h" },
  { value: 6, label: "6h" },
  { value: 24, label: "24h" },
  { value: 72, label: "3d" },
  { value: 168, label: "7d" },
];

// Trigger types whose match data is not persisted on the observation row.
// Surfaced as a yellow note when replay returns matched=0 but scanned>0.
const NON_REPLAYABLE_TRIGGERS = new Set([
  "audio_event",
  "clap_pattern",
  "speech_phrase",
]);

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Math.max(0, Date.now() - then);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function truncate(text: string | null, n: number): string {
  if (!text) return "";
  return text.length > n ? text.slice(0, n - 1) + "…" : text;
}

function cameraName(cameraId: string | null, cameras: Camera[]): string {
  if (!cameraId) return "Unknown camera";
  const cam = cameras.find((c) => c.id === cameraId);
  return cam ? cam.name : cameraId.slice(0, 8);
}

interface TestPanelProps {
  payload: () => RulePayload;
  existingRuleId: string | null;
  cameras: Camera[];
  className?: string;
}

export default function TestPanel({
  payload,
  existingRuleId,
  cameras,
  className,
}: TestPanelProps) {
  const { authFetch, token } = useAuth();

  const [testing, setTesting] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [testResult, setTestResult] = useState<RuleTestResponse | null>(null);
  const [replayResult, setReplayResult] = useState<RuleReplayResponse | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replayHours, setReplayHours] = useState<number>(24);
  const [showObs, setShowObs] = useState(false);
  // Track the payload used for the current test result, so we can hint
  // the user when the form has changed since their last test.
  const [testedSnapshot, setTestedSnapshot] = useState<string | null>(null);
  const [currentSnapshot, setCurrentSnapshot] = useState<string | null>(null);

  // Cancel in-flight requests when the user re-clicks or the panel
  // unmounts. AbortController is preserved across renders via refs.
  const testAbortRef = useRef<AbortController | null>(null);
  const replayAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      testAbortRef.current?.abort();
      replayAbortRef.current?.abort();
    };
  }, []);

  // Cheap "form has changed since last test" indicator. We re-snapshot
  // the payload every time the user interacts. This is best-effort.
  useEffect(() => {
    const t = setInterval(() => {
      try {
        setCurrentSnapshot(JSON.stringify(payload()));
      } catch {
        // ignore
      }
    }, 500);
    return () => clearInterval(t);
  }, [payload]);

  const formChangedSinceTest =
    testedSnapshot !== null &&
    currentSnapshot !== null &&
    testedSnapshot !== currentSnapshot;

  const runTest = async () => {
    testAbortRef.current?.abort();
    const ctl = new AbortController();
    testAbortRef.current = ctl;

    const body = payload();
    const snapshot = JSON.stringify(body);
    setTesting(true);
    setTestError(null);
    try {
      const res = await authFetch("/api/rules/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: snapshot,
        signal: ctl.signal,
      });
      if (!res.ok) {
        if (res.status === 422) {
          const data = await res.json().catch(() => null);
          const detail = data?.detail;
          let first = "Validation failed.";
          if (Array.isArray(detail) && detail.length > 0) {
            first = detail[0]?.msg || first;
          } else if (typeof detail === "string") {
            first = detail;
          }
          setTestError(`${first} Save validation failed. Fix the issue then try again.`);
          setTestResult(null);
          return;
        }
        if (res.status >= 500) {
          setTestError("Test temporarily unavailable.");
          setTestResult(null);
          return;
        }
        setTestError(`Request failed (${res.status}).`);
        setTestResult(null);
        return;
      }
      const data = (await res.json()) as RuleTestResponse;
      setTestResult(data);
      setTestedSnapshot(snapshot);
      setShowObs(false);
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") return;
      setTestError("Network error.");
      setTestResult(null);
    } finally {
      if (testAbortRef.current === ctl) {
        setTesting(false);
        testAbortRef.current = null;
      }
    }
  };

  const runReplay = async (hours: number) => {
    if (!existingRuleId) return;
    replayAbortRef.current?.abort();
    const ctl = new AbortController();
    replayAbortRef.current = ctl;
    // Client-side hard timeout. The backend can scan up to 10k rows.
    const timer = setTimeout(() => ctl.abort(), 30000);
    setReplaying(true);
    setReplayError(null);
    try {
      const res = await authFetch(
        `/api/rules/${existingRuleId}/replay?hours=${hours}`,
        { method: "POST", signal: ctl.signal },
      );
      if (!res.ok) {
        if (res.status >= 500) {
          setReplayError("Replay temporarily unavailable.");
        } else {
          setReplayError(`Request failed (${res.status}).`);
        }
        setReplayResult(null);
        return;
      }
      const data = (await res.json()) as RuleReplayResponse;
      setReplayResult(data);
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") {
        setReplayError("Replay timed out. Try a shorter window.");
      } else {
        setReplayError("Network error.");
      }
      setReplayResult(null);
    } finally {
      clearTimeout(timer);
      if (replayAbortRef.current === ctl) {
        setReplaying(false);
        replayAbortRef.current = null;
      }
    }
  };

  // Trigger pattern type must be set for /test to be meaningful.
  let triggerTypeSet = false;
  try {
    const p = payload();
    triggerTypeSet = Boolean(p.trigger_pattern?.type);
  } catch {
    triggerTypeSet = false;
  }

  const matchedTrigger = testResult?.matched_trigger;
  const matchedTriggerType =
    typeof testResult?.synthesized_observation?.observation_type === "string"
      ? (testResult.synthesized_observation.observation_type as string)
      : "";
  // Used to decide whether to show the "non-replayable trigger" yellow note.
  const isNonReplayable = NON_REPLAYABLE_TRIGGERS.has(matchedTriggerType);

  return (
    <div className={`bg-card border border-border rounded-lg p-4 space-y-3 ${className || ""}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-sm font-semibold text-foreground">Dry-run & replay</div>
          <div className="text-[11px] text-muted-foreground">
            Test the draft against a synthetic event, or replay against the last N hours of real observations.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={runTest}
            disabled={!triggerTypeSet || testing}
            className="px-3 py-1.5 text-sm rounded-md bg-accent text-accent-foreground font-medium hover:opacity-90 disabled:opacity-50"
            title={triggerTypeSet ? "Dry-run this rule against a synthetic observation" : "Pick a trigger type first"}
          >
            {testing ? "Testing." : formChangedSinceTest && testResult ? "Re-test now" : "Test rule"}
          </button>
          <div className="flex items-center gap-1">
            <select
              value={replayHours}
              onChange={(e) => setReplayHours(parseInt(e.target.value, 10))}
              disabled={!existingRuleId || replaying}
              className="px-2 py-1.5 text-xs rounded-md bg-background border border-border focus:outline-none focus:border-accent disabled:opacity-50"
              title="Replay window"
            >
              {REPLAY_HOURS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => runReplay(replayHours)}
              disabled={!existingRuleId || replaying}
              className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted disabled:opacity-50"
              title={existingRuleId ? "Replay against persisted observations" : "Save the rule first to enable replay"}
            >
              {replaying ? "Replaying." : "Replay"}
            </button>
          </div>
        </div>
      </div>

      {!triggerTypeSet && (
        <div className="text-[11px] text-muted-foreground">
          Pick a trigger type to enable Test.
        </div>
      )}

      {testError && (
        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2 whitespace-pre-wrap">
          {testError}
        </div>
      )}

      {testResult && (
        <div className="rounded-md border border-border bg-background/50 p-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`px-2 py-0.5 text-xs font-semibold rounded ${
                testResult.matched
                  ? "bg-green-500/20 text-green-400 border border-green-500/40"
                  : "bg-red-500/20 text-red-400 border border-red-500/40"
              }`}
            >
              {testResult.matched ? "Would fire" : "Would NOT fire"}
            </span>
            <span
              className={`px-1.5 py-0.5 text-[10px] rounded border ${
                matchedTrigger ? "border-green-500/40 text-green-400" : "border-zinc-500/40 text-zinc-400"
              }`}
            >
              trigger {matchedTrigger ? "ok" : "no"}
            </span>
            <span
              className={`px-1.5 py-0.5 text-[10px] rounded border ${
                testResult.matched_conditions ? "border-green-500/40 text-green-400" : "border-zinc-500/40 text-zinc-400"
              }`}
            >
              conditions {testResult.matched_conditions ? "ok" : "no"}
            </span>
            {testResult.schedule_blocked && (
              <span className="px-1.5 py-0.5 text-[10px] rounded border border-amber-500/40 text-amber-400">
                schedule blocked
              </span>
            )}
            {formChangedSinceTest && (
              <span className="px-1.5 py-0.5 text-[10px] rounded border border-blue-500/40 text-blue-300 ml-auto">
                form changed since test
              </span>
            )}
          </div>

          <div className="text-xs font-mono text-zinc-300 whitespace-pre-wrap">
            {testResult.reason}
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowObs((s) => !s)}
              className="text-[11px] text-blue-400 hover:underline"
            >
              {showObs ? "Hide" : "Open"} synthesized observation (we tested against this synthetic observation{showObs ? "" : ", open to inspect"})
            </button>
            {showObs && (
              <pre className="mt-2 text-[11px] font-mono bg-background border border-border rounded p-2 overflow-x-auto max-h-64">
                {JSON.stringify(testResult.synthesized_observation, null, 2)}
              </pre>
            )}
          </div>

          {testResult.would_fire.length > 0 && (
            <div className="space-y-2">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Would have fired ({testResult.would_fire.length})
              </div>
              {testResult.would_fire.map((a) => (
                <ActionPreview key={a.index} action={a} />
              ))}
            </div>
          )}
          {testResult.matched && testResult.would_fire.length === 0 && (
            <div className="text-[11px] text-muted-foreground italic">
              Matched, but no actions are configured on this rule yet.
            </div>
          )}
        </div>
      )}

      {replayError && (
        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2 whitespace-pre-wrap">
          {replayError}
        </div>
      )}

      {replayResult && (
        <div className="rounded-md border border-border bg-background/50 p-3 space-y-3">
          <div className="text-sm">
            <span className="font-semibold">
              Matched {replayResult.matched} of {replayResult.scanned} observations
            </span>{" "}
            <span className="text-muted-foreground">
              over the last {replayResult.hours}h.
            </span>
          </div>

          {(replayResult.first_matched_at || replayResult.last_matched_at) && (
            <div className="text-[11px] text-muted-foreground">
              First match {timeAgo(replayResult.first_matched_at)}
              {replayResult.last_matched_at && replayResult.last_matched_at !== replayResult.first_matched_at
                ? `, last match ${timeAgo(replayResult.last_matched_at)}`
                : ""}
              .
            </div>
          )}

          {replayResult.scanned === 0 && (
            <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded p-2">
              No observations to replay against yet. Wait for cameras to capture some frames.
            </div>
          )}

          {replayResult.scanned > 0 && replayResult.matched === 0 && (
            <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded p-2">
              {isNonReplayable
                ? `Trigger never fired against persisted observations. Some trigger types (audio_event, clap_pattern, speech_phrase, inline-geometry loitering, line_cross) cannot be replayed because their match data is not persisted on the observations table.`
                : `Trigger never fired against persisted observations. Some trigger types (audio_event, clap_pattern, speech_phrase, inline-geometry loitering, line_cross) cannot be replayed because their match data is not persisted on the observations table.`}
            </div>
          )}

          {replayResult.samples.length > 0 && (
            <div className="space-y-2">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Sample matches ({replayResult.samples.length})
              </div>
              {replayResult.samples.map((s) => (
                <div key={s.observation_id} className="flex gap-3 items-start border border-border/60 rounded p-2 bg-background">
                  {s.thumbnail_path ? (
                    <img
                      src={`/api/observations/${s.observation_id}/thumbnail${token ? `?token=${token}` : ""}`}
                      alt=""
                      className="w-20 h-14 rounded object-cover bg-muted flex-shrink-0"
                    />
                  ) : (
                    <div className="w-20 h-14 rounded bg-muted flex-shrink-0 flex items-center justify-center text-[10px] text-muted-foreground">
                      no thumb
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-xs">
                      <span className="font-medium">{cameraName(s.camera_id, cameras)}</span>
                      <span className="text-muted-foreground"> · {timeAgo(s.timestamp)}</span>
                    </div>
                    {s.snippet && (
                      <div className="text-[11px] text-zinc-400 mt-0.5">
                        {truncate(s.snippet, 140)}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActionPreview({ action }: { action: RuleTestActionPreview }) {
  const ra = action.rendered_action || {};
  const type = action.action_type;
  return (
    <div className="border border-border/60 rounded p-2 bg-background space-y-2">
      <div className="flex items-center gap-2">
        <span className="px-1.5 py-0.5 text-[10px] rounded bg-muted text-zinc-300">#{action.index}</span>
        <span className="px-1.5 py-0.5 text-[10px] rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">
          {type}
        </span>
        {typeof ra.url === "string" && (
          <span className="text-[11px] font-mono text-zinc-400 truncate">{ra.url as string}</span>
        )}
      </div>

      {type === "telegram" && typeof ra.text === "string" && (
        <div className="rounded-lg bg-sky-500/10 border border-sky-500/30 p-2 text-xs text-zinc-200 whitespace-pre-wrap">
          {ra.text as string}
        </div>
      )}
      {type === "telegram" && typeof ra.template === "string" && typeof ra.text !== "string" && (
        <div className="rounded-lg bg-sky-500/10 border border-sky-500/30 p-2 text-xs text-zinc-200 whitespace-pre-wrap">
          {ra.template as string}
        </div>
      )}

      {(type === "webhook" || type === "api_call") && ra.payload_template !== undefined && (
        <div>
          <div className="text-[10px] uppercase text-muted-foreground mb-1">payload</div>
          <pre className="text-[11px] font-mono bg-background border border-border rounded p-2 overflow-x-auto max-h-48">
            {JSON.stringify(ra.payload_template, null, 2)}
          </pre>
        </div>
      )}

      <details>
        <summary className="text-[10px] text-muted-foreground cursor-pointer hover:text-foreground">
          rendered action json
        </summary>
        <pre className="mt-1 text-[11px] font-mono bg-background border border-border rounded p-2 overflow-x-auto max-h-48">
          {JSON.stringify(ra, null, 2)}
        </pre>
      </details>
    </div>
  );
}
