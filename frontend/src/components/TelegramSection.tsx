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
  // Phase 3 fields. Webhook_secret is intentionally never sent over the wire.
  delivery_mode: "long_poll" | "webhook";
  webhook_url: string | null;
  media_quality: "off" | "low" | "high";
  rate_limit_per_chat_qps: number;
  rate_limit_per_chat_burst: number;
  dedupe_window_seconds: number;
  // Phase 4 household sharing.
  shared_with_household: boolean;
  share_permissions: "use" | "use_and_test";
  owned_by_me: boolean;
  owner_display_name: string | null;
  created_at: string;
}

interface WebhookInfo {
  url: string | null;
  has_custom_certificate: boolean;
  pending_update_count: number;
  last_error_date: number | null;
  last_error_message: string | null;
  ip_address: string | null;
  max_connections: number | null;
  backend_reachable: boolean | null;
  backend_probe_error: string | null;
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
      {/* Section card. Mirrors the Email card style */}
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [webhookInfo, setWebhookInfo] = useState<WebhookInfo | null>(null);
  const [webhookBusy, setWebhookBusy] = useState(false);
  const [webhookError, setWebhookError] = useState<string | null>(null);
  // Local sliders. So the user can drag without firing a PATCH per tick.
  // Committed onMouseUp / onBlur.
  const [qpsLocal, setQpsLocal] = useState<number>(channel.rate_limit_per_chat_qps);
  const [burstLocal, setBurstLocal] = useState<number>(channel.rate_limit_per_chat_burst);
  const [dedupeLocal, setDedupeLocal] = useState<number>(channel.dedupe_window_seconds);
  useEffect(() => {
    setQpsLocal(channel.rate_limit_per_chat_qps);
    setBurstLocal(channel.rate_limit_per_chat_burst);
    setDedupeLocal(channel.dedupe_window_seconds);
  }, [channel.rate_limit_per_chat_qps, channel.rate_limit_per_chat_burst, channel.dedupe_window_seconds]);

  const fetchWebhookInfo = useCallback(async () => {
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}/webhook-info`);
      if (res.ok) {
        setWebhookInfo(await res.json());
      } else {
        const body = await res.json().catch(() => null);
        setWebhookError(body?.detail || "Could not fetch webhook info");
      }
    } catch {
      setWebhookError("Network error fetching webhook info");
    }
  }, [authFetch, channel.id]);

  useEffect(() => {
    if (!showAdvanced) return;
    void fetchWebhookInfo();
  }, [showAdvanced, fetchWebhookInfo]);

  const switchDelivery = async (mode: "long_poll" | "webhook", dropPending = false) => {
    setWebhookBusy(true);
    setWebhookError(null);
    try {
      const res = await authFetch(`/api/telegram/channels/${channel.id}/delivery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, drop_pending_updates: dropPending }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setWebhookError(body?.detail || `Switch failed (${res.status}).`);
      } else {
        onChange();
        await fetchWebhookInfo();
      }
    } catch {
      setWebhookError("Network error.");
    } finally {
      setWebhookBusy(false);
    }
  };

  const requestSwitchToLongPoll = async () => {
    // Phase 3 UX note. If Telegram has pending updates queued, ask
    // before discarding them so the user doesn't lose acks/pairs in
    // flight.
    const pending = webhookInfo?.pending_update_count ?? 0;
    let drop = false;
    if (pending > 0) {
      drop = window.confirm(
        `Telegram has ${pending} unprocessed update${pending === 1 ? "" : "s"} queued.\n\n` +
          `OK = discard them and switch.\nCancel = keep them and switch anyway (they'll replay on next poll).`,
      );
    }
    await switchDelivery("long_poll", drop);
  };

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
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-medium truncate">{channel.label}</div>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${pill.cls}`}>
              {pill.label}
            </span>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                channel.delivery_mode === "webhook"
                  ? "bg-blue-500/15 text-blue-400 border-blue-500/30"
                  : "bg-muted text-muted-foreground border-border"
              }`}
              title={channel.delivery_mode === "webhook" ? "Updates arrive via webhook POST" : "Long-polling getUpdates"}
            >
              {channel.delivery_mode === "webhook" ? "Webhook" : "Long poll"}
            </span>
            {/* Phase 4. Owner badge + shared-by chip. */}
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                channel.owned_by_me
                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
                  : "bg-purple-500/15 text-purple-400 border-purple-500/30"
              }`}
              title={
                channel.owned_by_me
                  ? "You own this channel"
                  : `Shared by ${channel.owner_display_name || "another user"}. You can use it in your rules.`
              }
            >
              {channel.owned_by_me
                ? "You"
                : `Shared by ${channel.owner_display_name || "other"}`}
            </span>
            {channel.shared_with_household && channel.owned_by_me && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border bg-purple-500/15 text-purple-400 border-purple-500/30">
                Household
              </span>
            )}
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

          <label
            className={`flex items-center gap-1 text-[11px] text-muted-foreground select-none ${
              channel.owned_by_me ? "cursor-pointer" : "cursor-not-allowed opacity-50"
            }`}
            title={channel.owned_by_me ? "" : "Owner-only setting"}
          >
            <input
              type="checkbox"
              checked={channel.default_silent}
              disabled={savingField !== null || !channel.owned_by_me}
              onChange={(e) => patch({ default_silent: e.target.checked })}
              className="accent-green-500"
            />
            silent
          </label>
          <label
            className={`flex items-center gap-1 text-[11px] text-muted-foreground select-none ${
              channel.owned_by_me ? "cursor-pointer" : "cursor-not-allowed opacity-50"
            }`}
            title={channel.owned_by_me ? "" : "Owner-only setting"}
          >
            <input
              type="checkbox"
              checked={channel.enabled}
              disabled={savingField !== null || !channel.owned_by_me}
              onChange={(e) => patch({ enabled: e.target.checked })}
              className="accent-green-500"
            />
            enabled
          </label>

          {channel.pairing_status === "paired" && (channel.owned_by_me || channel.share_permissions === "use_and_test") && (
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
            onClick={() => setShowAdvanced((s) => !s)}
            className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground"
          >
            {showAdvanced ? "Hide advanced" : "Advanced"}
          </button>

          <button
            type="button"
            onClick={askDelete}
            disabled={!channel.owned_by_me}
            title={channel.owned_by_me ? "" : "Only the owner can delete a shared channel"}
            className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          >
            Delete
          </button>
        </div>
      </div>

      {showAdvanced && (
        <div className="mt-3 border-t border-border pt-3 space-y-4">
          {/* Phase 4. Household sharing. Visible to everyone; owner
              can toggle, non-owners see a tooltip explainer. */}
          <div>
            <div className="text-xs font-medium mb-1.5">Household sharing</div>
            {channel.owned_by_me ? (
              <>
                <label className="flex items-center gap-2 text-[11px] cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={channel.shared_with_household}
                    disabled={savingField !== null}
                    onChange={(e) =>
                      patch({ shared_with_household: e.target.checked } as Partial<TelegramChannel>)
                    }
                    className="accent-purple-500"
                  />
                  Share with household. Everyone can use this channel in their rules.
                </label>
                {channel.shared_with_household && (
                  <div className="mt-2">
                    <div className="text-[11px] text-muted-foreground mb-1">
                      Share permissions
                    </div>
                    <div className="flex gap-2">
                      {(["use", "use_and_test"] as const).map((p) => (
                        <label key={p} className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                          <input
                            type="radio"
                            name={`share-${channel.id}`}
                            checked={channel.share_permissions === p}
                            disabled={savingField !== null}
                            onChange={() =>
                              patch({ share_permissions: p } as Partial<TelegramChannel>)
                            }
                            className="accent-purple-500"
                          />
                          {p === "use" ? "Use only" : "Use and test"}
                        </label>
                      ))}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-1">
                      Token + chat binding stay yours. Others can pick this channel for their
                      rules. "Use and test" also lets them fire the Send test button.
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="text-[11px] text-muted-foreground bg-muted/30 border border-border rounded px-2 py-1.5">
                Shared by <span className="text-foreground/90 font-medium">{channel.owner_display_name || "another user"}</span>.
                You can pick this channel in your rules. Delete + token replacement are owner-only.
              </div>
            )}
          </div>

          {/* Owner-only knobs below. Non-owners see a short note. */}
          {!channel.owned_by_me && (
            <div className="text-[11px] text-muted-foreground">
              Delivery, media quality, rate limit, and dedupe are owner-only.
            </div>
          )}
          {channel.owned_by_me && (
          <>
          {/* Delivery mode */}
          <div>
            <div className="text-xs font-medium mb-1.5">Delivery mode</div>
            <div className="flex gap-2">
              <label className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                <input
                  type="radio"
                  name={`delivery-${channel.id}`}
                  checked={channel.delivery_mode === "long_poll"}
                  disabled={webhookBusy}
                  onChange={() => void requestSwitchToLongPoll()}
                  className="accent-green-500"
                />
                Long poll (default)
              </label>
              <label className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                <input
                  type="radio"
                  name={`delivery-${channel.id}`}
                  checked={channel.delivery_mode === "webhook"}
                  disabled={webhookBusy}
                  onChange={() => void switchDelivery("webhook")}
                  className="accent-blue-500"
                />
                Webhook (requires public URL)
              </label>
            </div>
            {webhookError && (
              <div className="mt-1.5 text-[11px] text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
                {webhookError}
                {/not set/i.test(webhookError) && (
                  <>
                    {" "}
                    <a href="/settings#system" className="underline">
                      Open System settings
                    </a>
                  </>
                )}
              </div>
            )}
            {channel.delivery_mode === "webhook" && webhookInfo && (
              <div className="mt-2 text-[11px] text-muted-foreground space-y-1">
                <div>
                  URL.{" "}
                  <span className="font-mono text-foreground/90 break-all">
                    {webhookInfo.url || "(not registered)"}
                  </span>
                </div>
                <div>
                  Pending updates.{" "}
                  <span
                    className={
                      webhookInfo.pending_update_count > 0
                        ? "text-amber-400 font-medium"
                        : "text-foreground/80"
                    }
                  >
                    {webhookInfo.pending_update_count}
                  </span>
                </div>
                {webhookInfo.pending_update_count > 0 && (
                  <div className="text-amber-400">
                    Telegram has {webhookInfo.pending_update_count} unprocessed updates.
                    Check your public URL is reachable.
                  </div>
                )}
                {webhookInfo.last_error_message && (
                  <div className="text-red-400">
                    Last error. {webhookInfo.last_error_message}
                  </div>
                )}
                {webhookInfo.backend_reachable === false && (
                  <div className="text-red-400">
                    Backend not reachable at the public URL.{" "}
                    {webhookInfo.backend_probe_error || "Webhook delivery will silently fail."}
                  </div>
                )}
                <div className="flex gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => void fetchWebhookInfo()}
                    className="px-2 py-0.5 text-[10px] rounded border border-border hover:bg-muted"
                  >
                    Refresh
                  </button>
                  <button
                    type="button"
                    disabled={webhookBusy}
                    onClick={async () => {
                      setWebhookBusy(true);
                      try {
                        await authFetch(
                          `/api/telegram/channels/${channel.id}/refresh-webhook`,
                          { method: "POST" },
                        );
                        await fetchWebhookInfo();
                        onChange();
                      } finally {
                        setWebhookBusy(false);
                      }
                    }}
                    className="px-2 py-0.5 text-[10px] rounded border border-border hover:bg-muted disabled:opacity-50"
                  >
                    Refresh URL with Telegram
                  </button>
                  <button
                    type="button"
                    onClick={async () => {
                      const res = await authFetch(
                        `/api/telegram/channels/${channel.id}/test-webhook-delivery`,
                        { method: "POST" },
                      );
                      const data = await res.json().catch(() => null);
                      if (data?.ok) {
                        alert(`Backend reachable at ${data?.probed_url || "public URL"}.`);
                      } else {
                        alert(`Backend NOT reachable. ${data?.error || "unknown error"}`);
                      }
                    }}
                    className="px-2 py-0.5 text-[10px] rounded border border-border hover:bg-muted"
                  >
                    Test webhook delivery
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Media quality */}
          <div>
            <div className="text-xs font-medium mb-1.5">Media quality</div>
            <div className="flex gap-2">
              {(["off", "low", "high"] as const).map((q) => (
                <label key={q} className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                  <input
                    type="radio"
                    name={`media-${channel.id}`}
                    checked={channel.media_quality === q}
                    disabled={savingField !== null}
                    onChange={() => void patch({ media_quality: q } as Partial<TelegramChannel>)}
                    className="accent-green-500"
                  />
                  {q === "off" ? "Off" : q === "low" ? "Low (720p, q70)" : "High (original)"}
                </label>
              ))}
            </div>
            <div className="text-[11px] text-muted-foreground mt-1">
              Low reduces bandwidth for outdoor cameras. Off sends text alerts only.
            </div>
          </div>

          {/* Per-chat rate limit */}
          <div>
            <div className="text-xs font-medium mb-1.5">Per-chat rate limit</div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-[11px] text-muted-foreground mb-0.5">
                  QPS. <span className="text-foreground/90">{qpsLocal.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min={0.2}
                  max={5.0}
                  step={0.1}
                  value={qpsLocal}
                  onChange={(e) => setQpsLocal(parseFloat(e.target.value))}
                  onMouseUp={() => void patch({ rate_limit_per_chat_qps: qpsLocal } as Partial<TelegramChannel>)}
                  onTouchEnd={() => void patch({ rate_limit_per_chat_qps: qpsLocal } as Partial<TelegramChannel>)}
                  className="w-full accent-green-500"
                />
              </div>
              <div>
                <div className="text-[11px] text-muted-foreground mb-0.5">
                  Burst. <span className="text-foreground/90">{burstLocal}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={10}
                  step={1}
                  value={burstLocal}
                  onChange={(e) => setBurstLocal(parseInt(e.target.value, 10))}
                  onMouseUp={() => void patch({ rate_limit_per_chat_burst: burstLocal } as Partial<TelegramChannel>)}
                  onTouchEnd={() => void patch({ rate_limit_per_chat_burst: burstLocal } as Partial<TelegramChannel>)}
                  className="w-full accent-green-500"
                />
              </div>
            </div>
            <div className="text-[11px] text-muted-foreground mt-1">
              Telegram limits group chats to 20 messages/minute. Tighten if you hit blockages.
            </div>
          </div>

          {/* Dedupe window */}
          <div>
            <div className="text-xs font-medium mb-1.5">
              Dedupe window.{" "}
              <span className="text-muted-foreground font-normal">{dedupeLocal}s</span>
            </div>
            <input
              type="range"
              min={0}
              max={300}
              step={5}
              value={dedupeLocal}
              onChange={(e) => setDedupeLocal(parseInt(e.target.value, 10))}
              onMouseUp={() => void patch({ dedupe_window_seconds: dedupeLocal } as Partial<TelegramChannel>)}
              onTouchEnd={() => void patch({ dedupe_window_seconds: dedupeLocal } as Partial<TelegramChannel>)}
              className="w-full accent-green-500"
            />
            <div className="text-[11px] text-muted-foreground mt-1">
              Suppresses identical messages within this window so a chatty rule doesn't spam.
            </div>
          </div>
          </>
          )}
        </div>
      )}

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
            <p className="text-xs text-muted-foreground mb-3">
              Telegram alerts are sent by a bot you create. it is free and takes about a
              minute. You will make a bot, then choose where it sends. a private chat, a
              group, or a channel.
            </p>
            <div className="rounded-md border border-border bg-background/50 p-3 mb-4 space-y-2">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Step 1. Create your bot and get its token
              </div>
              <ol className="text-xs text-muted-foreground space-y-1.5 list-decimal pl-4">
                <li>
                  Open{" "}
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                    className="text-accent hover:underline font-medium"
                  >
                    @BotFather
                  </a>{" "}
                  in Telegram (the official bot maker) and press <span className="font-medium">Start</span>.
                </li>
                <li>
                  Send <span className="font-mono text-foreground">/newbot</span>. It asks for a
                  name (e.g. <span className="text-foreground">Home Alerts</span>) and a username
                  ending in <span className="font-mono text-foreground">bot</span>.
                </li>
                <li>
                  BotFather replies with a <span className="font-medium text-foreground">token</span> that
                  looks like <span className="font-mono text-foreground">123456789:ABCdef...</span>. Copy it.
                </li>
                <li>Paste the token below. Nurby checks it instantly.</li>
              </ol>
            </div>
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
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
              Step 2. Choose where this bot sends alerts
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              A bot can only message places it has been added to, so pick a destination
              and connect it. Pairing tells Nurby the exact chat to send to. you can add
              more destinations later (e.g. Family, Security) and choose which one each
              rule alerts.
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
              <div className="text-xs text-muted-foreground space-y-2.5">
                <ol className="list-decimal pl-4 space-y-1.5">
                  <li>
                    Open the group or channel where you want alerts (or create a new one).
                  </li>
                  <li>
                    Add{" "}
                    <span className="font-mono text-foreground">@{pairChannel.bot_username || "your bot"}</span>{" "}
                    as a member. In a group, Add member. In a{" "}
                    <span className="text-foreground">channel</span>, add it as an{" "}
                    <span className="text-foreground font-medium">Administrator</span> with the
                    &ldquo;Post messages&rdquo; permission (channels only accept posts from admins).
                  </li>
                  <li>
                    Send this exact message in that group or channel so Nurby learns which chat it is.
                  </li>
                </ol>
                <div className="rounded-md bg-background border border-border px-3 py-2 font-mono text-xs select-all flex items-center justify-between gap-2">
                  <span>/pair {pair.nonce}</span>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard?.writeText(`/pair ${pair.nonce}`)}
                    className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground"
                  >
                    Copy
                  </button>
                </div>
                <p className="text-[11px] text-muted-foreground/80">
                  As soon as the bot sees that message, this dialog flips to Paired. no need to refresh.
                </p>
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
                  : `Waiting for Telegram. Expires in ${Math.max(0, secondsLeft)}s.`}
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
