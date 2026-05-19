"use client";

import { type NotifyDraft } from "../types";
import { StyledSelect } from "../StyledSelect";
import { VarInserter, type VarSpec } from "./VarInserter";

export interface NotifyEditorProps {
  draft: NotifyDraft;
  onChange: (next: NotifyDraft) => void;
  availableVars: VarSpec[];
}

export function NotifyEditor({ draft, onChange, availableVars }: NotifyEditorProps) {
  const d = draft;
  const set = (patch: Partial<NotifyDraft>) => onChange({ ...d, ...patch });
  return (
    <>
      <input
        type="text"
        value={d.message}
        onChange={(e) => set({ message: e.target.value })}
        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
        placeholder="Rule '{rule_name}' triggered"
      />
      <StyledSelect
        value={d.severity}
        options={[
          { value: "info", label: "Info" },
          { value: "warning", label: "Warning" },
          { value: "critical", label: "Critical" },
        ]}
        onChange={(v) => set({ severity: v })}
      />
      <div className="flex items-center gap-2">
        <VarInserter
          vars={availableVars}
          onInsert={(tok) => set({ message: d.message + tok })}
        />
      </div>
    </>
  );
}

export default NotifyEditor;
