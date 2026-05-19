"use client";

import { useState } from "react";

export interface VarSpec {
  name: string;
  keys: string[];
}

export interface VarInserterProps {
  vars: VarSpec[];
  onInsert: (token: string) => void;
}

// Insert-var dropdown. Appears next to template inputs when prior
// vlm_call cards declared outputs.
export function VarInserter({ vars, onInsert }: VarInserterProps) {
  const [open, setOpen] = useState(false);
  if (vars.length === 0) return null;
  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground"
      >
        Insert var
      </button>
      {open && (
        <div
          className="absolute z-10 mt-1 bg-card border border-border rounded shadow-lg min-w-[180px] max-h-64 overflow-y-auto"
          onMouseLeave={() => setOpen(false)}
        >
          {vars.map((v) => (
            <div key={v.name} className="py-1">
              <button
                type="button"
                onClick={() => {
                  onInsert(`{{vars.${v.name}}}`);
                  setOpen(false);
                }}
                className="block w-full text-left px-2 py-0.5 text-[10px] font-mono hover:bg-muted"
              >
                {`{{vars.${v.name}}}`}
              </button>
              {v.keys.map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => {
                    onInsert(`{{vars.${v.name}.${k}}}`);
                    setOpen(false);
                  }}
                  className="block w-full text-left pl-4 pr-2 py-0.5 text-[10px] font-mono hover:bg-muted"
                >
                  {`{{vars.${v.name}.${k}}}`}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default VarInserter;
