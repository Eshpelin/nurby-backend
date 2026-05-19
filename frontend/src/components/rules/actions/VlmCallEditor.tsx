"use client";

import { VLM_PROVIDERS, VLM_SCHEMA_PRESETS, type VlmCallDraft } from "../types";
import { StyledSelect } from "../StyledSelect";

export interface VlmCallEditorProps {
  draft: VlmCallDraft;
  onChange: (next: VlmCallDraft) => void;
}

export function VlmCallEditor({ draft, onChange }: VlmCallEditorProps) {
  const d = draft;
  const set = (patch: Partial<VlmCallDraft>) => onChange({ ...d, ...patch });
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Provider</label>
          <StyledSelect
            value={d.provider}
            options={VLM_PROVIDERS}
            onChange={(v) => set({ provider: v })}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Model</label>
          <input
            type="text"
            value={d.model}
            onChange={(e) => set({ model: e.target.value })}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
            placeholder="gpt-4o-mini"
          />
        </div>
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1">System prompt</label>
        <textarea
          value={d.system}
          onChange={(e) => set({ system: e.target.value })}
          rows={2}
          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm font-mono resize-y"
          placeholder="{{defaults.system}}"
        />
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1">User prompt</label>
        <textarea
          value={d.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          rows={3}
          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
        />
        <div className="flex flex-wrap gap-1 mt-1">
          {["description", "faces", "objects", "camera_name", "timestamp"].map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => set({ prompt: d.prompt + ` {{${k}}}` })}
              className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono"
            >{`{{${k}}}`}</button>
          ))}
        </div>
      </div>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={d.attachImage}
          onChange={(e) => set({ attachImage: e.target.checked })}
          className="accent-green-500"
        />
        <span className="text-xs">Attach snapshot image</span>
      </label>
      <div>
        <label className="flex items-center gap-2 cursor-pointer mb-1">
          <input
            type="checkbox"
            checked={d.useSchema}
            onChange={(e) => set({ useSchema: e.target.checked })}
            className="accent-green-500"
          />
          <span className="text-xs">Structured JSON output</span>
        </label>
        {d.useSchema && (
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
                  onClick={() => set({ schemaText: VLM_SCHEMA_PRESETS[p.key] })}
                  className="px-2 py-1 text-[11px] rounded border border-border hover:bg-muted text-muted-foreground"
                >
                  {p.label}
                </button>
              ))}
            </div>
            <textarea
              value={d.schemaText}
              onChange={(e) => set({ schemaText: e.target.value })}
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
            value={d.output}
            onChange={(e) => set({ output: e.target.value.replace(/[^\w]/g, "") })}
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
            value={d.maxRetries}
            onChange={(e) => set({ maxRetries: e.target.value })}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground block mb-1">Timeout (ms)</label>
          <input
            type="number"
            min={1000}
            step={1000}
            value={d.timeoutMs}
            onChange={(e) => set({ timeoutMs: e.target.value })}
            className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
          />
        </div>
      </div>
      <div>
        <label className="text-xs text-muted-foreground block mb-1">On error</label>
        <StyledSelect
          value={d.onError}
          options={[
            { value: "continue", label: "Continue chain" },
            { value: "stop", label: "Stop chain" },
            { value: "fallback", label: "Use fallback value" },
          ]}
          onChange={(v) => set({ onError: v })}
        />
      </div>
      <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
        Reference the result in later actions with {"{{"}vars.{d.output || "result"}.field{"}}"}.
      </div>
    </div>
  );
}

export default VlmCallEditor;
