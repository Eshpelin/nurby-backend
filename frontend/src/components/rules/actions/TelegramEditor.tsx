"use client";

import {
  TELEGRAM_TEMPLATE_VARS,
  TELEGRAM_DEFAULT_BUTTONS,
  TELEGRAM_BUTTON_ACTION_OPTIONS,
  TELEGRAM_BUTTON_DURATION_DEFAULTS,
  isValidHttpUrlOrTemplate,
  type TelegramDraft,
  type TelegramButton,
  type TelegramButtonAction,
  type TelegramChannelOption,
} from "../types";
import { StyledSelect } from "../StyledSelect";
import { VarInserter, type VarSpec } from "./VarInserter";

export interface TelegramEditorProps {
  draft: TelegramDraft;
  onChange: (next: TelegramDraft) => void;
  availableVars: VarSpec[];
  telegramChannels: TelegramChannelOption[];
  telegramChannelsLoading: boolean;
}

export function TelegramEditor({
  draft,
  onChange,
  availableVars,
  telegramChannels,
  telegramChannelsLoading,
}: TelegramEditorProps) {
  const d = draft;
  const set = (patch: Partial<TelegramDraft>) => onChange({ ...d, ...patch });
  const setButtons = (fn: (prev: TelegramButton[]) => TelegramButton[]) =>
    set({ buttons: fn(d.buttons) });
  const paired = telegramChannels.filter(
    (c) => c.enabled && c.pairing_status === "paired",
  );
  if (telegramChannelsLoading) {
    return <div className="text-xs text-muted-foreground">Loading Telegram channels.</div>;
  }
  if (paired.length === 0) {
    return (
      <div className="text-xs text-muted-foreground bg-muted/40 border border-border rounded px-3 py-2">
        No Telegram channels yet. Add one in{" "}
        <a href="/settings" className="underline text-accent">
          Settings → Notifications →
        </a>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-muted-foreground block mb-1">Telegram channel</label>
        <StyledSelect
          value={d.channelId}
          onChange={(v) => set({ channelId: v })}
          options={[
            { value: "", label: "Pick a channel..." },
            ...paired
              .slice()
              .sort((a, b) => a.label.localeCompare(b.label))
              .map((c) => ({
                value: c.id,
                label: `${c.label} · ${c.chat_title || "@" + (c.bot_username || "")}${
                  c.owned_by_me === false
                    ? ` (shared by ${c.owner_display_name || "other"})`
                    : ""
                }`,
              })),
          ]}
        />
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1">Message template</label>
        <textarea
          value={d.template}
          onChange={(e) => set({ template: e.target.value })}
          rows={4}
          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
          placeholder="<b>{rule_name}</b> on {camera_name}"
        />
        <div className="text-[10px] text-muted-foreground mt-1">
          HTML formatting is supported (e.g. &lt;b&gt;bold&lt;/b&gt;). Variables. Click to insert.
        </div>
        <div className="flex flex-wrap gap-1 mt-1 items-center">
          {TELEGRAM_TEMPLATE_VARS.map((v) => (
            <button
              key={v.key}
              type="button"
              title={v.desc}
              onClick={() => set({ template: d.template + `{${v.key}}` })}
              className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
            >
              {`{${v.key}}`}
            </button>
          ))}
          <VarInserter
            vars={availableVars}
            onInsert={(tok) => set({ template: d.template + tok })}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={d.silent}
            onChange={(e) => set({ silent: e.target.checked })}
            className="accent-green-500"
          />
          <span className="text-xs">Silent (no sound)</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={d.includeThumbnail}
            onChange={(e) => set({ includeThumbnail: e.target.checked })}
            className="accent-green-500"
          />
          <span className="text-xs">
            Include snapshot
            <span className="text-muted-foreground ml-1">(photo attachment)</span>
          </span>
        </label>
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-muted-foreground">
            Inline buttons ({d.buttons.length}/4)
          </label>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => set({ buttons: TELEGRAM_DEFAULT_BUTTONS })}
              className="text-[10px] px-2 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground"
            >
              Reset to defaults
            </button>
            <button
              type="button"
              disabled={d.buttons.length >= 4}
              onClick={() =>
                setButtons((prev) => [...prev, { label: "Action", action: "ack" }])
              }
              className="text-[10px] px-2 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50 disabled:cursor-not-allowed"
            >
              + Add button
            </button>
          </div>
        </div>
        {d.buttons.length === 0 ? (
          <div className="text-[11px] text-muted-foreground bg-muted/40 rounded px-2 py-1.5">
            No buttons. Recipients see a plain message.
          </div>
        ) : (
          <div className="space-y-1.5">
            {d.buttons.map((btn, i) => (
              <div
                key={i}
                className="flex flex-wrap gap-2 items-center bg-muted/30 border border-border rounded px-2 py-1.5"
              >
                <input
                  type="text"
                  value={btn.label}
                  onChange={(e) => {
                    const v = e.target.value;
                    setButtons((prev) =>
                      prev.map((b, idx) => (idx === i ? { ...b, label: v } : b)),
                    );
                  }}
                  placeholder="Label"
                  className="flex-1 min-w-[120px] px-2 py-1 rounded bg-background border border-border text-xs"
                />
                <StyledSelect
                  value={btn.action}
                  onChange={(val) => {
                    const action = val as TelegramButtonAction;
                    setButtons((prev) =>
                      prev.map((b, idx) => {
                        if (idx !== i) return b;
                        const next: TelegramButton = { ...b, action };
                        next.duration_seconds = TELEGRAM_BUTTON_DURATION_DEFAULTS[action];
                        if (action === "open" && !next.url) next.url = "{event_url}";
                        if (action !== "open") delete next.url;
                        return next;
                      }),
                    );
                  }}
                  options={TELEGRAM_BUTTON_ACTION_OPTIONS.map((o) => ({
                    value: o.value,
                    label: o.label,
                  }))}
                />
                {(btn.action === "mute_event" || btn.action === "snooze_rule") && (
                  <div className="flex items-center gap-1">
                    <input
                      type="range"
                      min={60}
                      max={3600}
                      step={60}
                      value={btn.duration_seconds ?? 600}
                      onChange={(e) => {
                        const v = parseInt(e.target.value) || 600;
                        setButtons((prev) =>
                          prev.map((b, idx) =>
                            idx === i ? { ...b, duration_seconds: v } : b,
                          ),
                        );
                      }}
                      className="w-24"
                    />
                    <span className="text-[10px] text-muted-foreground font-mono w-12">
                      {Math.round((btn.duration_seconds ?? 600) / 60)}m
                    </span>
                  </div>
                )}
                {btn.action === "open" && (
                  <input
                    type="text"
                    value={btn.url ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      setButtons((prev) =>
                        prev.map((b, idx) => (idx === i ? { ...b, url: v } : b)),
                      );
                    }}
                    placeholder="https://... or {event_url}"
                    className={`flex-1 min-w-[160px] px-2 py-1 rounded bg-background border text-xs ${
                      btn.url && !isValidHttpUrlOrTemplate(btn.url)
                        ? "border-red-500"
                        : "border-border"
                    }`}
                  />
                )}
                <button
                  type="button"
                  onClick={() => setButtons((prev) => prev.filter((_, idx) => idx !== i))}
                  className="text-[10px] px-2 py-1 rounded border border-border hover:bg-red-500/10 hover:border-red-500/40 text-muted-foreground"
                  title="Remove button"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default TelegramEditor;
