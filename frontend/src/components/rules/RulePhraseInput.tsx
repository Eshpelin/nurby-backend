"use client";

import { useState } from "react";

export function RulePhraseInput({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const cleaned = draft.split(",").map((s) => s.trim()).filter(Boolean);
    if (!cleaned.length) return;
    onChange(Array.from(new Set([...values, ...cleaned])));
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 min-h-[2.25rem] px-2 py-1 rounded-md border border-border bg-background focus-within:border-accent">
      {values.map((v) => (
        <span
          key={v}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-rose-500/15 text-rose-300 border border-rose-500/30"
        >
          {v}
          <button
            type="button"
            onClick={() => onChange(values.filter((x) => x !== v))}
            className="text-rose-300/70 hover:text-rose-200"
            aria-label={`Remove ${v}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          } else if (e.key === "Backspace" && !draft && values.length > 0) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={commit}
        placeholder={values.length === 0 ? placeholder : ""}
        className="flex-1 min-w-[10rem] bg-transparent text-sm focus:outline-none"
      />
    </div>
  );
}
