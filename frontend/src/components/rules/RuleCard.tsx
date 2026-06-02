"use client";

import { useState } from "react";
import { buildRuleSummary, describeTrigger, type Camera, type Rule } from "./types";

export interface RuleCardProps {
  rule: Rule;
  cameras: Camera[];
  selected: boolean;
  // Last-fired-at timestamp (ISO). Null/undefined renders "Never fired".
  lastFiredAt?: string | null;
  onSelect: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffMs = now - then;
  if (diffMs < 60_000) return "just now";
  const mins = Math.round(diffMs / 60_000);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

export function RuleCard({
  rule,
  cameras,
  selected,
  lastFiredAt,
  onSelect,
  onToggleEnabled,
  onEdit,
  onDuplicate,
  onDelete,
}: RuleCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  // Color the badge red-ish if Never AND rule older than 24h. Likely
  // a broken rule. Callers can investigate.
  const createdMs = rule.created_at ? new Date(rule.created_at).getTime() : 0;
  const olderThan24h = createdMs > 0 && Date.now() - createdMs > 24 * 3600 * 1000;
  const neverFired = !lastFiredAt;
  const badgeClass = neverFired
    ? olderThan24h
      ? "border-red-800 bg-red-900/30 text-red-400"
      : "border-border bg-muted/40 text-muted-foreground"
    : "border-border bg-muted/40 text-muted-foreground";

  return (
    <div
      onClick={onSelect}
      className={`rounded-lg border p-4 cursor-pointer transition-colors ${
        selected
          ? "border-accent bg-card"
          : "border-border bg-card hover:border-muted-foreground/30"
      } ${rule.enabled ? "" : "opacity-60"}`}
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
            <div className="font-medium flex items-center gap-2">
              <span>{rule.name}</span>
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${badgeClass}`}
                title={lastFiredAt ? `Last fired ${lastFiredAt}` : "No events recorded for this rule"}
              >
                {neverFired ? "Never fired" : `Fired ${formatRelative(lastFiredAt!)}`}
              </span>
              {!rule.enabled && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground">
                  Disabled
                </span>
              )}
            </div>
            <div className="text-xs text-muted-foreground mt-0.5">
              {describeTrigger(rule.trigger_pattern)}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 relative">
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
              setMenuOpen((v) => !v);
            }}
            className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
            title="More actions"
          >
            ⋯
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-1 bg-card border border-border rounded shadow-lg z-10 min-w-[140px]"
              onClick={(e) => e.stopPropagation()}
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                onClick={() => {
                  setMenuOpen(false);
                  onDuplicate();
                }}
                className="block w-full text-left px-3 py-1.5 text-xs hover:bg-muted"
              >
                Duplicate
              </button>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  onToggleEnabled();
                }}
                className="block w-full text-left px-3 py-1.5 text-xs hover:bg-muted"
              >
                {rule.enabled ? "Disable" : "Enable"}
              </button>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  if (confirm(`Delete rule "${rule.name}"?`)) onDelete();
                }}
                className="block w-full text-left px-3 py-1.5 text-xs hover:bg-red-900/30 text-red-400"
              >
                Delete
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="mt-2 text-xs italic text-muted-foreground/80 leading-relaxed">
        {buildRuleSummary(rule, cameras)}
      </div>
    </div>
  );
}
