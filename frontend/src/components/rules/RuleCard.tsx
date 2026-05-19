"use client";

import { buildRuleSummary, describeTrigger, type Camera, type Rule } from "./types";

export interface RuleCardProps {
  rule: Rule;
  cameras: Camera[];
  selected: boolean;
  onSelect: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

export function RuleCard({
  rule,
  cameras,
  selected,
  onSelect,
  onToggleEnabled,
  onEdit,
  onDelete,
}: RuleCardProps) {
  return (
    <div
      onClick={onSelect}
      className={`rounded-lg border p-4 cursor-pointer transition-colors ${
        selected
          ? "border-accent bg-card"
          : "border-border bg-card hover:border-muted-foreground/30"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleEnabled();
            }}
            className={`w-8 h-5 rounded-full relative transition-colors ${
              rule.enabled ? "bg-green-500" : "bg-muted"
            }`}
          >
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                rule.enabled ? "left-3.5" : "left-0.5"
              }`}
            />
          </button>
          <div>
            <div className="font-medium">{rule.name}</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              {describeTrigger(rule.trigger_pattern)}
            </div>
          </div>
        </div>
        <div className="flex gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
            className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
          >
            Edit
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors"
          >
            Del
          </button>
        </div>
      </div>
      <div className="mt-2 text-xs italic text-muted-foreground/80 leading-relaxed">
        {buildRuleSummary(rule, cameras)}
      </div>
    </div>
  );
}
