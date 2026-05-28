"use client";

import {
  HTTP_METHODS,
  AUTH_TYPES,
  TEMPLATE_VARIABLES,
  DEFAULT_PAYLOAD_TEMPLATE,
  type WebhookDraft,
} from "../types";
import { VarInserter, type VarSpec } from "./VarInserter";

export interface WebhookEditorProps {
  draft: WebhookDraft;
  onChange: (next: WebhookDraft) => void;
  availableVars: VarSpec[];
}

export function WebhookEditor({ draft, onChange, availableVars }: WebhookEditorProps) {
  const d = draft;
  const set = (patch: Partial<WebhookDraft>) => onChange({ ...d, ...patch });
  return (
    <div className="space-y-3">
      {d.type === "api_call" && (
        <div>
          <label className="text-xs text-muted-foreground block mb-1">HTTP Method</label>
          <div className="flex gap-1">
            {HTTP_METHODS.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => set({ method: m })}
                className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                  d.method === m
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
        value={d.url}
        onChange={(e) => set({ url: e.target.value })}
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
              onClick={() => set({ authType: at.value })}
              className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                d.authType === at.value
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border hover:bg-muted"
              }`}
            >
              {at.label}
            </button>
          ))}
        </div>
        {d.authType === "bearer" && (
          <input
            type="password"
            value={d.authToken}
            onChange={(e) => set({ authToken: e.target.value })}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
            placeholder="Bearer token"
          />
        )}
        {d.authType === "api_key" && (
          <div className="flex gap-2">
            <input
              type="text"
              value={d.authHeader}
              onChange={(e) => set({ authHeader: e.target.value })}
              className="w-1/3 px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="Header name"
            />
            <input
              type="password"
              value={d.authKey}
              onChange={(e) => set({ authKey: e.target.value })}
              className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="API key value"
            />
          </div>
        )}
        {d.authType === "basic" && (
          <div className="flex gap-2">
            <input
              type="text"
              value={d.authUser}
              onChange={(e) => set({ authUser: e.target.value })}
              className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="Username"
            />
            <input
              type="password"
              value={d.authPass}
              onChange={(e) => set({ authPass: e.target.value })}
              className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
              placeholder="Password"
            />
          </div>
        )}
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1.5">
          Sign body (HMAC-SHA256)
        </label>
        <input
          type="password"
          value={d.secret}
          onChange={(e) => set({ secret: e.target.value })}
          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono"
          placeholder="Shared secret (optional)"
        />
        <p className="text-[10px] text-muted-foreground mt-1">
          When set, Nurby signs the exact request body and sends
          {" "}
          <span className="font-mono">X-Nurby-Signature</span>. Your receiver
          recomputes the HMAC to verify the alert came from Nurby. Required for
          the physical device presets on an untrusted network.
        </p>
      </div>
      <div>
        <label className="flex items-center gap-2 cursor-pointer mb-2">
          <input
            type="checkbox"
            checked={d.useCustomPayload}
            onChange={(e) => {
              const checked = e.target.checked;
              set({
                useCustomPayload: checked,
                payloadTemplate:
                  checked && !d.payloadTemplate ? DEFAULT_PAYLOAD_TEMPLATE : d.payloadTemplate,
                payloadError: "",
              });
            }}
            className="accent-green-500"
          />
          <span className="text-xs">Custom payload template</span>
        </label>
        {d.useCustomPayload && (
          <div className="space-y-2">
            <textarea
              value={d.payloadTemplate}
              onChange={(e) => {
                const v = e.target.value;
                let err = "";
                try {
                  if (v.trim()) JSON.parse(v);
                } catch {
                  err = "Invalid JSON";
                }
                set({ payloadTemplate: v, payloadError: err });
              }}
              rows={8}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
              placeholder={DEFAULT_PAYLOAD_TEMPLATE}
              spellCheck={false}
            />
            {d.payloadError && (
              <div className="text-[10px] text-red-400">{d.payloadError}</div>
            )}
            <div className="flex items-center gap-2 flex-wrap">
              <div className="text-[10px] text-muted-foreground">Vars.</div>
              {TEMPLATE_VARIABLES.map((v) => (
                <button
                  key={v.key}
                  type="button"
                  title={v.desc}
                  onClick={() =>
                    set({ payloadTemplate: d.payloadTemplate + `"{{${v.key}}}"` })
                  }
                  className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                >
                  {`{{${v.key}}}`}
                </button>
              ))}
              <VarInserter
                vars={availableVars}
                onInsert={(tok) =>
                  set({ payloadTemplate: d.payloadTemplate + `"${tok}"` })
                }
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default WebhookEditor;
