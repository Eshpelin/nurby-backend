"use client";

/**
 * Telegram notifications settings section.
 *
 * Renders the card row in the Settings page plus the multi-step modal
 * for adding a channel and guiding the user through pairing.
 *
 * Phase 1 design notes:
 *  - QR rendering uses `api.qrserver.com` since the project has no
 *    local QR library. The URL is short and stable; the user can also
 *    tap the deep link directly. If the asset host is unreachable,
 *    pairing still works via the link or the manual /pair command.
 *  - The pairing modal polls GET /channels/{id} every 2 seconds while
 *    on step 2; we stop polling on success, modal close, or after the
 *    nonce TTL elapses.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";

export interface TelegramChannel {
  id: string;
  label: string;
  bot_username: string | null;
  chat_id: string | null;
  chat_title: string | null;
  chat_type: string | null;
  default_silent: boolean;
  enabled: boolean;
  paired_at: string | null;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_error: string | null;
  pairing_status: "pending" | "paired" | "blocked" | "disabled" | "error";
  created_at: string;
}

interface PairInit {
  nonce: string;
  deep_link: string;
  qr_payload: string;
  expires_in_seconds: number;
}

interface TestResult {
  ok: boolean;
  message_id?: number | null;
  error?: string | null;
}

function statusPill(c: TelegramChannel): { label: string; cls: string } {
  switch (c.pairing_status) {
    case "paired":
      return { label: "Paired", cls: "bg-green-500/15 text-green-400 border-green-500/30" };
    case "pending":
      return { label: "Pending pairing", cls: "bg-amber-500/15 text-amber-400 border-amber-500/30" };
    case "blocked":
      return { label: "Blocked", cls: "bg-red-500/15 text-red-400 border-red-500/30" };
    case "disabled":
      return { label: "Disabled", cls: "bg-muted text-muted-foreground border-border" };
    case "error":
    default:
      return { label: "Error", cls: "bg-red-500/15 text-red-400 border-red-500/30" };
  }
}

export default function TelegramSection() {
  const { authFetch } = useAuth();
  const [channels, setChannels] = useState<TelegramChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [pairingChannelId, setPairingChannelId] = useState<string | null>(null);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await authFetch("/api/telegram/channels");
      if (res.ok) setChannels(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    fetchChannels();
  }, [fetchChannels]);

  const openAdd = () => {
    setPairingChannelId(null);
    setShowModal(true);
  };

  const resumePairing = (channelId: string) => {
    setPairingChannelId(channelId);
    setShowModal(true);
  };

  const enabledPairedCount = channels.filter((c) => c.pairing_status === "paired").length;
  const pendingCount = channels.filter((c) => c.pairing_status === "pending").length;

  return (
    <>
      {/* Section card. mirrors the Email card style */}
      <div className="rounded-lg border border-border bg-card px-4 py-3.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                channels.length === 0
                  ? "bg-muted-foreground/40"
                  : enabledPairedCount > 0
                  ? "bg-green-500"
                  : "bg-amber-500"
              }`}
            />
            <div>
              <div className="text-sm font-medium">Telegram alerts</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {loading
                  ? "Loading."
                  : channels.length === 0
                  ? "No channels yet. Add a Telegram bot to receive alerts."
                  : `${enabledPairedCount} paired${pendingCount > 0 ? `, ${pendingCount} pending` : ""}.`}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={openAdd}
            className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted transition-colors"
          >
            Add Telegram channel
          </button>
        </div>

        {channels.length > 0 && (
          <div className="mt-4 space-y-2">
            {channels.map((c) => (
              <ChannelRow
                key={c.id}
                channel={c}
                onChange={fetchChannels}
                onResumePair={() => resumePairing(c.id)}
              />
            ))}
          </div>
        )}
      </div>

      {showModal && (
        <AddOrPairModal
          existingChannelId={pairingChannelId}
          onClose={() => {
            setShowModal(false);
            setPairingChannelId(null);
            fetchChannels();
          }}
          onChannelChange={fetchChannels}
        />
      )}
    </>
  );
}

function ChannelRow({
  channel,
  onChange,
  onResumePair,
}: {
  channel: TelegramChannel;
  onChange: () => void;
  onResumePair: () => void;
}) {
  const { authFetch } = useAuth();
  const pill = statusPill(channel);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [ruleCount, setRuleCount] = useState<number | null>(null);

  const patch = async (patchBody: Partial<TelegramChannel>) => {
    setSavingField(Object.keys(patchBody)[0] || null);
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patchBody),
      });
      if (res.ok) onChange();
    } catch {
      /* silent */
    } finally {
      setSavingField(null);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}/test`, {
        method: "POST",
      });
      const data = await res.json();
      setTestResult(data);
      if (res.ok || res.status === 200) onChange();
    } catch {
      setTestResult({ ok: false, error: "Network error" });
    } finally {
      setTesting(false);
    }
  };

  const askDelete = async () => {
    setShowDelete(true);
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}/rule-usage`);
      if (res.ok) {
        const data = await res.json();
        setRuleCount(typeof data.rule_count === "number" ? data.rule_count : 0);
      }
    } catch {
      setRuleCount(0);
    }
  };

  const confirmDelete = async () => {
    setDeleting(true);
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}`, {
        method: "DELETE",
      });
      if (res.ok || res.status === 204) {
        onChange();
        setShowDelete(false);
      }
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-background/40 px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-sm font-medium truncate">{channel.label}</div>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${pill.cls}`}>
              {pill.label}
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground mt-0.5 truncate">
            {channel.bot_username ? <span>@{channel.bot_username}</span> : <span>(no bot)</span>}
            {channel.chat_title ? <span> · {channel.chat_title}</span> : null}
            {channel.chat_type ? <span> · {channel.chat_type}</span> : null}
          </div>
          {channel.last_error && channel.pairing_status !== "paired" && (
            <div className="text-[11px] text-red-400 mt-0.5 truncate">{channel.last_error}</div>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {channel.pairing_status === "pending" && (
            <button
              type="button"
              onClick={onResumePair}
              className="px-2 py-1 text-[11px] rounded border border-amber-500/40 text-amber-400 hover:bg-amber-500/10"
            >
              Resume pairing
            </button>
          )}

          {channel.pairing_status === "blocked" && (
            <button
              type="button"
              onClick={async () => {
                await patch({ enabled: true });
                await runTest();
              }}
              className="px-2 py-1 text-[11px] rounded border border-red-500/40 text-red-400 hover:bg-red-500/10"
            >
              Re-enable
            </button>
          )}

          <label className="flex items-center gap-1 text-[11px] text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={channel.default_silent}
              disabled={savingField !== null}
              onChange={(e) => patch({ default_silent: e.target.checked })}
              className="accent-green-500"
            />
            silent
          </label>
          <label className="flex items-center gap-1 text-[11px] text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={channel.enabled}
              disabled={savingField !== null}
              onChange={(e) => patch({ enabled: e.target.checked })}
              className="accent-green-500"
            />
            enabled
          </label>

          {channel.pairing_status === "paired" && (
            <button
              type="button"
              disabled={testing}
              onClick={runTest}
              className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted disabled:opacity-50"
            >
              {testing ? "Sending." : "Send test"}
            </button>
          )}

          <button
            type="button"
            onClick={askDelete}
            className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground"
          >
            Delete
          </button>
        </div>
      </div>

      {testResult && (
        <div
          className={`mt-2 text-[11px] rounded px-2 py-1 ${
            testResult.ok
              ? "bg-green-500/10 text-green-400 border border-green-500/30"
              : "bg-red-500/10 text-red-400 border border-red-500/30"
          }`}
        >
          {testResult.ok
            ? `Sent ✓ (message id ${testResult.message_id ?? "?"})`
            : `Failed. ${testResult.error || "Telegram rejected the send."}`}
        </div>
      )}

      {showDelete && (
        <div className="mt-3 border-t border-border pt-3">
          <div className="text-xs text-foreground/90 mb-2">
            This will stop alerts to{" "}
            <span className="font-medium">{channel.chat_title || channel.label}</span>. Existing
            rules using this channel will silently no-op until you point them at another channel.
          </div>
          <div className="text-[11px] text-muted-foreground mb-3">
            {ruleCount === null
              ? "Checking rule usage."
              : ruleCount === 0
              ? "No rules reference this channel."
              : `${ruleCount} rule${ruleCount === 1 ? "" : "s"} currently reference this channel.`}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={deleting}
              onClick={confirmDelete}
              className="px-3 py-1.5 text-xs rounded border border-red-500/40 text-red-400 hover:bg-red-500/10 disabled:opacity-50"
            >
              {deleting ? "Deleting." : "Delete channel"}
            </button>
            <button
              type="button"
              onClick={() => setShowDelete(false)}
              className="px-3 py-1.5 text-xs rounded border border-border hover:bg-muted"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AddOrPairModal({
  existingChannelId,
  onClose,
  onChannelChange,
}: {
  existingChannelId: string | null;
  onClose: () => void;
  onChannelChange: () => void;
}) {
  const { authFetch } = useAuth();
  // Steps. 1 = enter token, 2 = pair, 3 = success
  const [step, setStep] = useState<1 | 2 | 3>(existingChannelId ? 2 : 1);
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [channelId, setChannelId] = useState<string | null>(existingChannelId);
  const [pair, setPair] = useState<PairInit | null>(null);
  const [pairTab, setPairTab] = useState<"dm" | "group">("dm");
  const [pairChannel, setPairChannel] = useState<TelegramChannel | null>(null);
  const [pairExpired, setPairExpired] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }, []);

  useEffect(() => {
    return stopPolling;
  }, [stopPolling]);

  const beginPair = useCallback(
    async (id: string) => {
      setError(null);
      try {
        const res = await authFetch(`/api/telegram/channels/${id}/pair-init`, {
          method: "POST",
        });
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          setError(body?.detail || "Could not start pairing.");
          return;
        }
        const data: PairInit = await res.json();
        setPair(data);
        setPairExpired(false);
        setSecondsLeft(data.expires_in_seconds);
      } catch {
        setError("Network error starting pairing.");
      }
    },
    [authFetch]
  );

  // When we arrive at step 2 with a known channelId, kick off pairing.
  useEffect(() => {
    if (step !== 2 || !channelId) return;
    beginPair(channelId);
  }, [step, channelId, beginPair]);

  // Poll for pairing completion every 2s and count down the nonce TTL.
  useEffect(() => {
    if (step !== 2 || !channelId || !pair) return;
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const res = await authFetch(`/api/telegram/channels/${channelId}`);
        if (res.ok) {
          const data: TelegramChannel = await res.json();
          setPairChannel(data);
          if (data.pairing_status === "paired") {
            stopPolling();
            setStep(3);
            onChannelChange();
          }
        }
      } catch {
        /* silent */
      }
    }, 2000);

    tickRef.current = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          setPairExpired(true);
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          if (tickRef.current) {
            clearInterval(tickRef.current);
            tickRef.current = null;
          }
          return 0;
        }
        return s - 1;
      });
    }, 1000);

    return stopPolling;
  }, [step, channelId, pair, authFetch, stopPolling, onChannelChange]);

  const submitStep1 = async () => {
    setError(null);
    if (!label.trim()) {
      setError("Label is required.");
      return;
    }
    if (!token.trim()) {
      setError("Bot token is required.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await authFetch("/api/telegram/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label.trim(), bot_token: token.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.detail || `Telegram rejected the token (${res.status}).`);
        return;
      }
      const ch: TelegramChannel = await res.json();
      setChannelId(ch.id);
      setStep(2);
      onChannelChange();
    } catch {
      setError("Network error. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const restartPair = async () => {
    if (!channelId) return;
    await beginPair(channelId);
  };

  const sendTest = async () => {
    if (!channelId) return;
    try {
      await authFetch(`/api/telegram/channels/${channelId}/test`, { method: "POST" });
      onChannelChange();
    } catch {
      /* silent */
    }
  };

  const qrSrc = useMemo(() => {
    if (!pair) return null;
    // Fallback to api.qrserver.com — no local QR lib available. Phase 2
    // can swap this for a local renderer (e.g. qrcode.react).
    return `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(
      pair.qr_payload
    )}`;
  }, [pair]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-2">
          <h2 className="text-lg font-semibold">
            {step === 1 ? "Add Telegram channel" : step === 2 ? "Pair with Telegram" : "Paired"}
          </h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground text-lg leading-none"
          >
            ×
          </button>
        </div>

        {step === 1 && (
          <>
            <p className="text-xs text-muted-foreground mb-4">
              Telegram alerts use a bot you own. Open Telegram, DM @BotFather, send /newbot,
              follow the prompts, then paste the bot token here. Takes about 30 seconds.
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Label
                </label>
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="Family alerts"
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground block mb-1">
                  Bot token
                </label>
                <div className="flex gap-2">
                  <input
                    type={showToken ? "text" : "password"}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="123456:ABC-DEF..."
                    className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken((s) => !s)}
                    className="px-3 py-2 text-xs rounded-md border border-border hover:bg-muted"
                  >
                    {showToken ? "Hide" : "Show"}
                  </button>
                </div>
              </div>
              {error && (
                <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1.5">
                  {error}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={submitStep1}
                  className="px-3 py-1.5 text-xs rounded-md border border-green-500/40 bg-green-500/10 text-green-400 hover:bg-green-500/20 disabled:opacity-50"
                >
                  {submitting ? "Validating." : "Continue"}
                </button>
              </div>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <p className="text-xs text-muted-foreground mb-3">
              Now add the bot to where you want alerts.
            </p>
            <div className="flex gap-1 mb-3">
              <button
                type="button"
                onClick={() => setPairTab("dm")}
                className={`flex-1 px-3 py-1.5 text-xs rounded border ${
                  pairTab === "dm"
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border hover:bg-muted"
                }`}
              >
                Direct message
              </button>
              <button
                type="button"
                onClick={() => setPairTab("group")}
                className={`flex-1 px-3 py-1.5 text-xs rounded border ${
                  pairTab === "group"
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border hover:bg-muted"
                }`}
              >
                Group / channel
              </button>
            </div>

            {pairTab === "dm" && pair && (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Tap the link below or scan the QR. Telegram opens, hit Start.
                </p>
                <a
                  href={pair.deep_link}
                  target="_blank"
                  rel="noreferrer"
                  className="block text-center px-4 py-3 rounded-md border border-accent/40 bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20"
                >
                  Open in Telegram
                </a>
                {qrSrc && (
                  <div className="flex justify-center">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={qrSrc}
                      alt="Telegram pairing QR"
                      width={240}
                      height={240}
                      className="rounded border border-border bg-white p-1"
                    />
                  </div>
                )}
              </div>
            )}

            {pairTab === "group" && pair && pairChannel && (
              <div className="text-xs text-muted-foreground space-y-2">
                <p>
                  Add{" "}
                  <span className="font-mono text-foreground">
                    @{pairChannel.bot_username || "bot"}
                  </span>{" "}
                  to your group, then send this in the group.
                </p>
                <div className="rounded-md bg-background border border-border px-3 py-2 font-mono text-xs select-all">
                  /pair {pair.nonce}
                </div>
              </div>
            )}

            {pairTab === "group" && pair && !pairChannel && (
              <div className="text-xs text-muted-foreground">
                Loading bot details.
              </div>
            )}

            <div className="mt-4 text-[11px] text-muted-foreground flex items-center justify-between">
              <span>
                {pairExpired
                  ? "Pairing link expired."
                  : `Waiting for Telegram. expires in ${Math.max(0, secondsLeft)}s.`}
              </span>
              {pairExpired && (
                <button
                  type="button"
                  onClick={restartPair}
                  className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted"
                >
                  Try again
                </button>
              )}
            </div>
            {error && (
              <div className="mt-2 text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1.5">
                {error}
              </div>
            )}
          </>
        )}

        {step === 3 && (
          <div className="space-y-3">
            <div className="rounded-md border border-green-500/30 bg-green-500/10 text-green-400 text-sm px-3 py-2">
              Paired ✓
              {pairChannel?.chat_title ? (
                <span className="ml-1 text-foreground/90">
                  with{" "}
                  <span className="font-medium">{pairChannel.chat_title}</span>
                </span>
              ) : null}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={sendTest}
                className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted"
              >
                Send test
              </button>
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 text-xs rounded-md border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
