"use client";

import {
  TEMPLATE_VARIABLES,
  DEFAULT_PAYLOAD_TEMPLATE,
  type BroadcastDraft,
} from "../types";
import { VarInserter, type VarSpec } from "./VarInserter";

export interface BroadcastEditorProps {
  draft: BroadcastDraft;
  onChange: (next: BroadcastDraft) => void;
  availableVars: VarSpec[];
}

export function BroadcastEditor({ draft, onChange, availableVars }: BroadcastEditorProps) {
  const d = draft;
  const set = (patch: Partial<BroadcastDraft>) => onChange({ ...d, ...patch });
  return (
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
        <span className="text-xs">Custom broadcast payload</span>
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
            rows={6}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
            placeholder={DEFAULT_PAYLOAD_TEMPLATE}
            spellCheck={false}
          />
          {d.payloadError && (
            <div className="text-[10px] text-red-400">{d.payloadError}</div>
          )}
          <div className="flex flex-wrap gap-1 items-center">
            {TEMPLATE_VARIABLES.map((v) => (
              <button
                key={v.key}
                type="button"
                title={v.desc}
                onClick={() =>
                  set({ payloadTemplate: d.payloadTemplate + `"{{${v.key}}}"` })
                }
                className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
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
  );
}

export default BroadcastEditor;
