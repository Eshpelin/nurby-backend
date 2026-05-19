"use client";

import { StyledSelect } from "./StyledSelect";

export function ModelClassPicker({
  value,
  onChange,
  activeModels,
  classes,
  loading,
  anyLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  activeModels: string[];
  classes: string[];
  loading: boolean;
  anyLabel: string;
}) {
  const needsModel = activeModels.length === 0;
  const options = [
    { value: "", label: anyLabel },
    ...classes.map((l) => ({ value: l, label: l })),
  ];

  return (
    <div className="space-y-2">
      {activeModels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          <span className="text-[10px] text-muted-foreground self-center">Labels sourced from.</span>
          {activeModels.map((m) => (
            <span key={m} className="px-1.5 py-0.5 text-[10px] font-mono rounded border border-border bg-muted/30 text-muted-foreground">
              {m}
            </span>
          ))}
        </div>
      )}
      {needsModel ? (
        <div className="rounded-md border border-dashed border-amber-500/40 bg-amber-500/5 p-2.5 text-[11px] text-amber-300">
          No detection model configured on the selected camera(s). Add one on the{" "}
          <a href="/cameras" className="underline hover:text-amber-200">camera settings</a> page first. Labels come from whichever model you pick.
        </div>
      ) : loading ? (
        <p className="text-[11px] text-muted-foreground">Loading labels from model.</p>
      ) : classes.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          Model loaded no classes. First-run download may still be running. Refresh in a moment.
        </p>
      ) : (
        <StyledSelect value={value} options={options} onChange={onChange} />
      )}
    </div>
  );
}
