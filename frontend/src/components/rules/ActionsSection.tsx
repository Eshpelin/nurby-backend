"use client";

import {
  ACTION_TYPES,
  HTTP_METHODS,
  AUTH_TYPES,
  TEMPLATE_VARIABLES,
  DEFAULT_PAYLOAD_TEMPLATE,
  TELEGRAM_TEMPLATE_VARS,
  TELEGRAM_DEFAULT_BUTTONS,
  TELEGRAM_BUTTON_ACTION_OPTIONS,
  TELEGRAM_BUTTON_DURATION_DEFAULTS,
  VLM_PROVIDERS,
  VLM_SCHEMA_PRESETS,
  isValidHttpUrlOrTemplate,
  type TelegramButton,
  type TelegramButtonAction,
  type TelegramChannelOption,
} from "./types";
import { StyledSelect } from "./StyledSelect";

export interface ActionsSectionProps {
  telegramChannels: TelegramChannelOption[];
  telegramChannelsLoading: boolean;

  formActionType: string;
  setFormActionType: (v: string) => void;
  formActionUrl: string;
  setFormActionUrl: (v: string) => void;
  formActionMethod: string;
  setFormActionMethod: (v: string) => void;
  formActionMessage: string;
  setFormActionMessage: (v: string) => void;
  formActionSeverity: string;
  setFormActionSeverity: (v: string) => void;
  formActionAuthType: string;
  setFormActionAuthType: (v: string) => void;
  formActionAuthToken: string;
  setFormActionAuthToken: (v: string) => void;
  formActionAuthHeader: string;
  setFormActionAuthHeader: (v: string) => void;
  formActionAuthKey: string;
  setFormActionAuthKey: (v: string) => void;
  formActionAuthUser: string;
  setFormActionAuthUser: (v: string) => void;
  formActionAuthPass: string;
  setFormActionAuthPass: (v: string) => void;
  formActionPayloadTemplate: string;
  setFormActionPayloadTemplate: (updater: string | ((prev: string) => string)) => void;
  formActionUseCustomPayload: boolean;
  setFormActionUseCustomPayload: (v: boolean) => void;
  formPayloadError: string;
  setFormPayloadError: (v: string) => void;
  formActionEmailTo: string;
  setFormActionEmailTo: (v: string) => void;
  formActionEmailSubject: string;
  setFormActionEmailSubject: (v: string) => void;
  formActionEmailBody: string;
  setFormActionEmailBody: (updater: string | ((prev: string) => string)) => void;

  formActionTelegramChannelId: string;
  setFormActionTelegramChannelId: (v: string) => void;
  formActionTelegramTemplate: string;
  setFormActionTelegramTemplate: (updater: string | ((prev: string) => string)) => void;
  formActionTelegramSilent: boolean;
  setFormActionTelegramSilent: (v: boolean) => void;
  formActionTelegramThumbnail: boolean;
  setFormActionTelegramThumbnail: (v: boolean) => void;
  formActionTelegramButtons: TelegramButton[];
  setFormActionTelegramButtons: (updater: TelegramButton[] | ((prev: TelegramButton[]) => TelegramButton[])) => void;

  formVlmProvider: string;
  setFormVlmProvider: (v: string) => void;
  formVlmModel: string;
  setFormVlmModel: (v: string) => void;
  formVlmSystem: string;
  setFormVlmSystem: (v: string) => void;
  formVlmPrompt: string;
  setFormVlmPrompt: (updater: string | ((prev: string) => string)) => void;
  formVlmAttachImage: boolean;
  setFormVlmAttachImage: (v: boolean) => void;
  formVlmUseSchema: boolean;
  setFormVlmUseSchema: (v: boolean) => void;
  formVlmSchemaText: string;
  setFormVlmSchemaText: (v: string) => void;
  formVlmOutput: string;
  setFormVlmOutput: (v: string) => void;
  formVlmMaxRetries: string;
  setFormVlmMaxRetries: (v: string) => void;
  formVlmOnError: string;
  setFormVlmOnError: (v: string) => void;
  formVlmTimeoutMs: string;
  setFormVlmTimeoutMs: (v: string) => void;
}

export function ActionsSection(props: ActionsSectionProps) {
  const {
    telegramChannels,
    telegramChannelsLoading,
    formActionType,
    setFormActionType,
    formActionUrl,
    setFormActionUrl,
    formActionMethod,
    setFormActionMethod,
    formActionMessage,
    setFormActionMessage,
    formActionSeverity,
    setFormActionSeverity,
    formActionAuthType,
    setFormActionAuthType,
    formActionAuthToken,
    setFormActionAuthToken,
    formActionAuthHeader,
    setFormActionAuthHeader,
    formActionAuthKey,
    setFormActionAuthKey,
    formActionAuthUser,
    setFormActionAuthUser,
    formActionAuthPass,
    setFormActionAuthPass,
    formActionPayloadTemplate,
    setFormActionPayloadTemplate,
    formActionUseCustomPayload,
    setFormActionUseCustomPayload,
    formPayloadError,
    setFormPayloadError,
    formActionEmailTo,
    setFormActionEmailTo,
    formActionEmailSubject,
    setFormActionEmailSubject,
    formActionEmailBody,
    setFormActionEmailBody,
    formActionTelegramChannelId,
    setFormActionTelegramChannelId,
    formActionTelegramTemplate,
    setFormActionTelegramTemplate,
    formActionTelegramSilent,
    setFormActionTelegramSilent,
    formActionTelegramThumbnail,
    setFormActionTelegramThumbnail,
    formActionTelegramButtons,
    setFormActionTelegramButtons,
    formVlmProvider,
    setFormVlmProvider,
    formVlmModel,
    setFormVlmModel,
    formVlmSystem,
    setFormVlmSystem,
    formVlmPrompt,
    setFormVlmPrompt,
    formVlmAttachImage,
    setFormVlmAttachImage,
    formVlmUseSchema,
    setFormVlmUseSchema,
    formVlmSchemaText,
    setFormVlmSchemaText,
    formVlmOutput,
    setFormVlmOutput,
    formVlmMaxRetries,
    setFormVlmMaxRetries,
    formVlmOnError,
    setFormVlmOnError,
    formVlmTimeoutMs,
    setFormVlmTimeoutMs,
  } = props;

  return (
    <fieldset className="border border-border rounded-md p-3 space-y-3">
      <legend className="text-xs font-medium text-muted-foreground px-1">
        Action
      </legend>
      <StyledSelect
        value={formActionType}
        options={ACTION_TYPES.map((a) => ({ value: a.value, label: a.label }))}
        onChange={setFormActionType}
      />

      {(formActionType === "webhook" || formActionType === "api_call") && (
        <div className="space-y-3">
          {formActionType === "api_call" && (
            <div>
              <label className="text-xs text-muted-foreground block mb-1">HTTP Method</label>
              <div className="flex gap-1">
                {HTTP_METHODS.map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setFormActionMethod(m)}
                    className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                      formActionMethod === m
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border hover:bg-muted"
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>
          )}

          <input
            type="url"
            value={formActionUrl}
            onChange={(e) => setFormActionUrl(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
            placeholder="https://api.example.com/endpoint"
          />

          <div>
            <label className="text-xs text-muted-foreground block mb-1.5">Authentication</label>
            <div className="flex gap-1 mb-2">
              {AUTH_TYPES.map((at) => (
                <button
                  key={at.value}
                  type="button"
                  onClick={() => setFormActionAuthType(at.value)}
                  className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                    formActionAuthType === at.value
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {at.label}
                </button>
              ))}
            </div>

            {formActionAuthType === "bearer" && (
              <input
                type="password"
                value={formActionAuthToken}
                onChange={(e) => setFormActionAuthToken(e.target.value)}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                placeholder="Bearer token"
              />
            )}

            {formActionAuthType === "api_key" && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={formActionAuthHeader}
                  onChange={(e) => setFormActionAuthHeader(e.target.value)}
                  className="w-1/3 px-3 py-2 rounded-md bg-background border border-border text-sm"
                  placeholder="Header name"
                />
                <input
                  type="password"
                  value={formActionAuthKey}
                  onChange={(e) => setFormActionAuthKey(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                  placeholder="API key value"
                />
              </div>
            )}

            {formActionAuthType === "basic" && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={formActionAuthUser}
                  onChange={(e) => setFormActionAuthUser(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                  placeholder="Username"
                />
                <input
                  type="password"
                  value={formActionAuthPass}
                  onChange={(e) => setFormActionAuthPass(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                  placeholder="Password"
                />
              </div>
            )}
          </div>

          <div>
            <label className="flex items-center gap-2 cursor-pointer mb-2">
              <input
                type="checkbox"
                checked={formActionUseCustomPayload}
                onChange={(e) => {
                  setFormActionUseCustomPayload(e.target.checked);
                  if (e.target.checked && !formActionPayloadTemplate) {
                    setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                  }
                  setFormPayloadError("");
                }}
                className="accent-green-500"
              />
              <span className="text-xs">Custom payload template</span>
            </label>

            {formActionUseCustomPayload && (
              <div className="space-y-2">
                <textarea
                  value={formActionPayloadTemplate}
                  onChange={(e) => {
                    setFormActionPayloadTemplate(e.target.value);
                    setFormPayloadError("");
                    try {
                      if (e.target.value.trim()) JSON.parse(e.target.value);
                    } catch {
                      setFormPayloadError("Invalid JSON");
                    }
                  }}
                  rows={8}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                  placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                  spellCheck={false}
                />
                {formPayloadError && (
                  <div className="text-[10px] text-red-400">{formPayloadError}</div>
                )}
                <div>
                  <div className="text-[10px] text-muted-foreground mb-1">
                    Available variables (click to insert)
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {TEMPLATE_VARIABLES.map((v) => (
                      <button
                        key={v.key}
                        type="button"
                        title={v.desc}
                        onClick={() => {
                          setFormActionPayloadTemplate(
                            (prev) => prev + `"{{${v.key}}}"`,
                          );
                        }}
                        className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                      >
                        {`{{${v.key}}}`}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {formActionType === "broadcast" && (
        <div>
          <label className="flex items-center gap-2 cursor-pointer mb-2">
            <input
              type="checkbox"
              checked={formActionUseCustomPayload}
              onChange={(e) => {
                setFormActionUseCustomPayload(e.target.checked);
                if (e.target.checked && !formActionPayloadTemplate) {
                  setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                }
                setFormPayloadError("");
              }}
              className="accent-green-500"
            />
            <span className="text-xs">Custom broadcast payload</span>
          </label>

          {formActionUseCustomPayload && (
            <div className="space-y-2">
              <textarea
                value={formActionPayloadTemplate}
                onChange={(e) => {
                  setFormActionPayloadTemplate(e.target.value);
                  setFormPayloadError("");
                  try {
                    if (e.target.value.trim()) JSON.parse(e.target.value);
                  } catch {
                    setFormPayloadError("Invalid JSON");
                  }
                }}
                rows={6}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                spellCheck={false}
              />
              {formPayloadError && (
                <div className="text-[10px] text-red-400">{formPayloadError}</div>
              )}
              <div className="flex flex-wrap gap-1">
                {TEMPLATE_VARIABLES.map((v) => (
                  <button
                    key={v.key}
                    type="button"
                    title={v.desc}
                    onClick={() => {
                      setFormActionPayloadTemplate(
                        (prev) => prev + `"{{${v.key}}}"`,
                      );
                    }}
                    className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                  >
                    {`{{${v.key}}}`}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {formActionType === "notify" && (
        <>
          <input
            type="text"
            value={formActionMessage}
            onChange={(e) => setFormActionMessage(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
            placeholder="Rule '{rule_name}' triggered"
          />
          <StyledSelect
            value={formActionSeverity}
            options={[
              { value: "info", label: "Info" },
              { value: "warning", label: "Warning" },
              { value: "critical", label: "Critical" },
            ]}
            onChange={setFormActionSeverity}
          />
        </>
      )}

      {formActionType === "email" && (
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Recipient</label>
            <input
              type="email"
              value={formActionEmailTo}
              onChange={(e) => setFormActionEmailTo(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="user@example.com"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Subject template</label>
            <input
              type="text"
              value={formActionEmailSubject}
              onChange={(e) => setFormActionEmailSubject(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="Nurby alert. {{rule_name}}"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Body template</label>
            <textarea
              value={formActionEmailBody}
              onChange={(e) => setFormActionEmailBody(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
              placeholder="Rule {{rule_name}} fired at {{timestamp}} on camera {{camera_id}}"
            />
          </div>
          <div>
            <div className="text-[10px] text-muted-foreground mb-1">
              Available variables (click to insert into body)
            </div>
            <div className="flex flex-wrap gap-1">
              {TEMPLATE_VARIABLES.map((v) => (
                <button
                  key={v.key}
                  type="button"
                  title={v.desc}
                  onClick={() => {
                    setFormActionEmailBody(
                      (prev) => prev + `{{${v.key}}}`,
                    );
                  }}
                  className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                >
                  {`{{${v.key}}}`}
                </button>
              ))}
            </div>
          </div>
          <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            SMTP must be configured in Settings for email delivery to work.
          </div>
        </div>
      )}

      {formActionType === "telegram" && (
        <div className="space-y-3">
          {telegramChannelsLoading ? (
            <div className="text-xs text-muted-foreground">Loading Telegram channels.</div>
          ) : telegramChannels.filter((c) => c.enabled && c.pairing_status === "paired").length === 0 ? (
            <div className="text-xs text-muted-foreground bg-muted/40 border border-border rounded px-3 py-2">
              No Telegram channels yet. Add one in{" "}
              <a href="/settings" className="underline text-accent">
                Settings → Notifications →
              </a>
            </div>
          ) : (
            <>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">
                  Telegram channel
                </label>
                <StyledSelect
                  value={formActionTelegramChannelId}
                  onChange={setFormActionTelegramChannelId}
                  options={[
                    { value: "", label: "Pick a channel..." },
                    ...telegramChannels
                      .filter((c) => c.enabled && c.pairing_status === "paired")
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
                <label className="text-xs text-muted-foreground block mb-1">
                  Message template
                </label>
                <textarea
                  value={formActionTelegramTemplate}
                  onChange={(e) => setFormActionTelegramTemplate(e.target.value)}
                  rows={4}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
                  placeholder="<b>{rule_name}</b> on {camera_name}"
                />
                <div className="text-[10px] text-muted-foreground mt-1">
                  HTML formatting is supported (e.g. &lt;b&gt;bold&lt;/b&gt;). Variables. click to insert.
                </div>
                <div className="flex flex-wrap gap-1 mt-1">
                  {TELEGRAM_TEMPLATE_VARS.map((v) => (
                    <button
                      key={v.key}
                      type="button"
                      title={v.desc}
                      onClick={() =>
                        setFormActionTelegramTemplate((prev) => prev + `{${v.key}}`)
                      }
                      className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
                    >
                      {`{${v.key}}`}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formActionTelegramSilent}
                    onChange={(e) => setFormActionTelegramSilent(e.target.checked)}
                    className="accent-green-500"
                  />
                  <span className="text-xs">Silent (no sound, overrides channel default)</span>
                </label>
                <label
                  className="flex items-center gap-2 cursor-pointer"
                  title="Sends the observation snapshot as a Telegram photo. Files >10MB fall back to a link."
                >
                  <input
                    type="checkbox"
                    checked={formActionTelegramThumbnail}
                    onChange={(e) => setFormActionTelegramThumbnail(e.target.checked)}
                    className="accent-green-500"
                  />
                  <span className="text-xs">
                    Include snapshot
                    <span className="text-muted-foreground ml-1">
                      (photo attachment, &gt;10MB falls back to a link)
                    </span>
                  </span>
                </label>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs text-muted-foreground">
                    Inline buttons ({formActionTelegramButtons.length}/4)
                  </label>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => setFormActionTelegramButtons(TELEGRAM_DEFAULT_BUTTONS)}
                      className="text-[10px] px-2 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground"
                    >
                      Reset to defaults
                    </button>
                    <button
                      type="button"
                      disabled={formActionTelegramButtons.length >= 4}
                      onClick={() => {
                        setFormActionTelegramButtons((prev) => [
                          ...prev,
                          { label: "Action", action: "ack" },
                        ]);
                      }}
                      className="text-[10px] px-2 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      + Add button
                    </button>
                  </div>
                </div>

                {formActionTelegramButtons.length === 0 ? (
                  <div className="text-[11px] text-muted-foreground bg-muted/40 rounded px-2 py-1.5">
                    No buttons. Recipients see a plain message.
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {formActionTelegramButtons.map((btn, i) => (
                      <div
                        key={i}
                        className="flex flex-wrap gap-2 items-center bg-muted/30 border border-border rounded px-2 py-1.5"
                      >
                        <input
                          type="text"
                          value={btn.label}
                          onChange={(e) => {
                            const v = e.target.value;
                            setFormActionTelegramButtons((prev) =>
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
                            setFormActionTelegramButtons((prev) =>
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
                                setFormActionTelegramButtons((prev) =>
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
                              setFormActionTelegramButtons((prev) =>
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
                          onClick={() => {
                            setFormActionTelegramButtons((prev) =>
                              prev.filter((_, idx) => idx !== i),
                            );
                          }}
                          className="text-[10px] px-2 py-1 rounded border border-border hover:bg-red-500/10 hover:border-red-500/40 text-muted-foreground"
                          title="Remove button"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {formActionTelegramButtons.some(
                  (b) => b.action === "open" && (b.url || "").includes("{event_url}"),
                ) && (
                  <div className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded px-2 py-1">
                    Set the public base URL in settings to enable View clip buttons. Otherwise the button will be dropped at send time.
                  </div>
                )}
              </div>

              <div className="text-[11px] text-muted-foreground bg-muted/40 rounded px-2 py-1.5">
                {(() => {
                  const ch = telegramChannels.find(
                    (c) => c.id === formActionTelegramChannelId,
                  );
                  const target = ch
                    ? ch.chat_title || `@${ch.bot_username || "bot"}`
                    : "the selected channel";
                  const parts: string[] = [`Send a Telegram message to ${target}`];
                  if (formActionTelegramThumbnail) parts.push("with snapshot");
                  if (formActionTelegramButtons.length > 0) {
                    parts.push(
                      `with ${formActionTelegramButtons.length} inline button${
                        formActionTelegramButtons.length === 1 ? "" : "s"
                      }`,
                    );
                  }
                  return parts.join(" ") + ".";
                })()}
              </div>
            </>
          )}
        </div>
      )}

      {formActionType === "vlm_call" && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Provider</label>
              <StyledSelect
                value={formVlmProvider}
                options={VLM_PROVIDERS}
                onChange={setFormVlmProvider}
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Model</label>
              <input
                type="text"
                value={formVlmModel}
                onChange={(e) => setFormVlmModel(e.target.value)}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                placeholder="gpt-4o-mini"
              />
            </div>
          </div>

          <div>
            <label className="text-xs text-muted-foreground block mb-1">System prompt</label>
            <textarea
              value={formVlmSystem}
              onChange={(e) => setFormVlmSystem(e.target.value)}
              rows={2}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono resize-y"
              placeholder="{{defaults.system}}"
            />
            <label className="flex items-center gap-2 cursor-pointer mt-1">
              <input
                type="checkbox"
                checked={formVlmSystem.startsWith("{{defaults.system}}")}
                onChange={(e) => {
                  if (e.target.checked && !formVlmSystem.startsWith("{{defaults.system}}")) {
                    setFormVlmSystem(`{{defaults.system}}\n\n${formVlmSystem}`);
                  } else if (!e.target.checked) {
                    setFormVlmSystem(
                      formVlmSystem.replace(/^\{\{defaults\.system\}\}\n*/, ""),
                    );
                  }
                }}
                className="accent-green-500"
              />
              <span className="text-[11px] text-muted-foreground">Extend global default</span>
            </label>
          </div>

          <div>
            <label className="text-xs text-muted-foreground block mb-1">User prompt</label>
            <textarea
              value={formVlmPrompt}
              onChange={(e) => setFormVlmPrompt(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
            />
            <div className="flex flex-wrap gap-1 mt-1">
              {["description", "faces", "objects", "camera_name", "timestamp"].map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setFormVlmPrompt((p) => p + ` {{${k}}}`)}
                  className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
                >{`{{${k}}}`}</button>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={formVlmAttachImage}
              onChange={(e) => setFormVlmAttachImage(e.target.checked)}
              className="accent-green-500"
            />
            <span className="text-xs">Attach snapshot image</span>
          </label>

          <div>
            <label className="flex items-center gap-2 cursor-pointer mb-1">
              <input
                type="checkbox"
                checked={formVlmUseSchema}
                onChange={(e) => setFormVlmUseSchema(e.target.checked)}
                className="accent-green-500"
              />
              <span className="text-xs">Structured JSON output</span>
            </label>
            {formVlmUseSchema && (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-1">
                  {[
                    { key: "threat", label: "Threat level" },
                    { key: "notify", label: "Notify yes/no" },
                    { key: "intent", label: "Intent classifier" },
                    { key: "entities", label: "Entity counts" },
                  ].map((p) => (
                    <button
                      key={p.key}
                      type="button"
                      onClick={() => setFormVlmSchemaText(VLM_SCHEMA_PRESETS[p.key])}
                      className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground"
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <textarea
                  value={formVlmSchemaText}
                  onChange={(e) => setFormVlmSchemaText(e.target.value)}
                  rows={8}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono resize-y"
                />
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Output variable</label>
              <input
                type="text"
                value={formVlmOutput}
                onChange={(e) => setFormVlmOutput(e.target.value.replace(/[^\w]/g, ""))}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
                placeholder="result"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Max retries</label>
              <input
                type="number"
                min={0}
                max={3}
                value={formVlmMaxRetries}
                onChange={(e) => setFormVlmMaxRetries(e.target.value)}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Timeout (ms)</label>
              <input
                type="number"
                min={1000}
                step={1000}
                value={formVlmTimeoutMs}
                onChange={(e) => setFormVlmTimeoutMs(e.target.value)}
                className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">On error</label>
            <StyledSelect
              value={formVlmOnError}
              options={[
                { value: "continue", label: "Continue chain" },
                { value: "stop", label: "Stop chain" },
                { value: "fallback", label: "Use fallback value" },
              ]}
              onChange={setFormVlmOnError}
            />
          </div>
          <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            Reference the result in later actions with {"{{"}vars.{formVlmOutput || "result"}.field{"}}"}.
          </div>
        </div>
      )}
    </fieldset>
  );
}
