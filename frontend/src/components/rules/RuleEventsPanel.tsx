"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  buildRuleSummary,
  describeActions,
  describeTrigger,
  type Camera,
  type EventEntry,
  type Rule,
} from "./types";
import { SummaryCard } from "./SummaryCard";
import { EventNotesPanel } from "./EventNotesPanel";

export interface RuleEventsPanelProps {
  selectedRule: Rule | null;
  cameras: Camera[];
}

export function RuleEventsPanel({ selectedRule, cameras }: RuleEventsPanelProps) {
  const { authFetch } = useAuth();
  const [ruleEvents, setRuleEvents] = useState<EventEntry[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  const fetchRuleEvents = useCallback(async (ruleId: string) => {
    setEventsLoading(true);
    try {
      const res = await authFetch(`/api/events/history?rule_id=${ruleId}&limit=20`);
      if (res.ok) setRuleEvents(await res.json());
    } catch {
      /* silent */
    } finally {
      setEventsLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    if (!selectedRule) {
      setRuleEvents([]);
      return;
    }
    fetchRuleEvents(selectedRule.id);
    const interval = setInterval(() => fetchRuleEvents(selectedRule.id), 30000);
    return () => clearInterval(interval);
  }, [selectedRule, fetchRuleEvents]);

  return (
    <aside className="col-span-4">
      <div className="sticky top-20 rounded-lg border border-border bg-card p-5">
        <div className="flex items-center gap-2 mb-4">
          <span className="w-1.5 h-1.5 rounded-full bg-accent pulse-dot" />
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Preview
          </span>
        </div>
        {selectedRule ? (
          <div className="space-y-3 text-sm">
            <SummaryCard text={buildRuleSummary(selectedRule, cameras)} />
            <div>
              <span className="text-muted-foreground text-xs">Name</span>
              <div className="font-medium">{selectedRule.name}</div>
            </div>
            <div>
              <span className="text-muted-foreground text-xs">Status</span>
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    selectedRule.enabled ? "bg-green-500" : "bg-yellow-500"
                  }`}
                />
                {selectedRule.enabled ? "Active" : "Disabled"}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground text-xs">Trigger</span>
              <div>{describeTrigger(selectedRule.trigger_pattern)}</div>
            </div>
            {selectedRule.conditions && Object.keys(selectedRule.conditions).length > 0 && (
              <div>
                <span className="text-muted-foreground text-xs">Conditions</span>
                <div className="text-xs mt-1 space-y-1">
                  {(() => {
                    const cond = selectedRule.conditions!;
                    const camIds = (cond.camera_ids as string[]) || (cond.camera_id ? [cond.camera_id as string] : []);
                    const parts: string[] = [];
                    if (camIds.length > 0) {
                      const names = camIds.map((cid) => {
                        const cam = cameras.find((c) => c.id === cid);
                        return cam ? cam.name : cid.slice(0, 8);
                      });
                      parts.push(`Cameras. ${names.join(", ")}`);
                    }
                    const days = cond.days as string[] | undefined;
                    if (days && days.length > 0 && days.length < 7) {
                      parts.push(`Days. ${days.map((d) => d.charAt(0).toUpperCase() + d.slice(1)).join(", ")}`);
                    }
                    if (cond.time_after || cond.time_before) {
                      parts.push(`Hours. ${cond.time_after || "00:00"} to ${cond.time_before || "23:59"}`);
                    }
                    if (cond.min_confidence) {
                      const mc = cond.min_confidence as number;
                      const label = mc >= 0.8 ? "Very high" : mc >= 0.6 ? "High" : mc >= 0.4 ? "Medium" : "Low";
                      parts.push(`Confidence. ${label} (${Math.round(mc * 100)}%+)`);
                    }
                    return parts.map((p, i) => <div key={i}>{p}</div>);
                  })()}
                </div>
              </div>
            )}
            <div>
              <span className="text-muted-foreground text-xs">Actions</span>
              <div>{describeActions(selectedRule.actions)}</div>
            </div>
            <div>
              <span className="text-muted-foreground text-xs">Cooldown</span>
              <div>{selectedRule.cooldown_seconds}s between fires</div>
            </div>
            <div>
              <span className="text-muted-foreground text-xs">Created</span>
              <div>{new Date(selectedRule.created_at).toLocaleString()}</div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground leading-relaxed">
            Select a rule to see its configuration preview.
          </p>
        )}
      </div>

      {selectedRule && (
        <div className="mt-4 rounded-lg border border-border bg-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Execution Log
            </span>
          </div>
          {eventsLoading && ruleEvents.length === 0 ? (
            <p className="text-sm text-muted-foreground">Loading events.</p>
          ) : ruleEvents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events fired yet for this rule.</p>
          ) : (
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {ruleEvents.map((ev) => (
                <div
                  key={ev.id}
                  onClick={() => setExpandedEventId(expandedEventId === ev.id ? null : ev.id)}
                  className="rounded-md border border-border bg-background p-3 cursor-pointer hover:border-muted-foreground/30 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          ev.action_status === "success"
                            ? "bg-green-500"
                            : ev.action_status === "failed"
                            ? "bg-red-500"
                            : "bg-yellow-500"
                        }`}
                      />
                      {ev.action_type && (
                        <span className="px-1.5 py-0.5 text-[10px] rounded bg-muted text-muted-foreground font-mono">
                          {ev.action_type}
                        </span>
                      )}
                      {(ev.acked_at || ev.acknowledged_at) && (
                        <span
                          className="px-1.5 py-0.5 text-[10px] rounded bg-green-500/15 text-green-400 border border-green-500/30"
                          title={
                            ev.acked_via
                              ? `Acknowledged via ${ev.acked_via}`
                              : "Acknowledged"
                          }
                        >
                          {ev.acked_via === "telegram"
                            ? "✓ Acked (Telegram)"
                            : ev.acked_via === "web"
                            ? "✓ Acked (web)"
                            : ev.acked_via === "api"
                            ? "✓ Acked (API)"
                            : "✓ Acked"}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      {new Date(ev.fired_at).toLocaleString()}
                    </span>
                  </div>
                  {ev.action_status === "failed" && ev.action_error && (
                    <div className="mt-1.5 text-[11px] text-red-400 truncate">
                      {ev.action_error}
                    </div>
                  )}
                  {expandedEventId === ev.id && (
                    <div className="mt-3 pt-3 border-t border-border">
                      <div className="text-[10px] text-muted-foreground mb-1">Payload</div>
                      <pre className="text-[10px] font-mono bg-muted/50 rounded p-2 overflow-x-auto max-h-40 overflow-y-auto whitespace-pre-wrap">
                        {ev.payload ? JSON.stringify(ev.payload, null, 2) : "No payload"}
                      </pre>
                      {ev.action_error && (
                        <div className="mt-2">
                          <div className="text-[10px] text-muted-foreground mb-1">Error</div>
                          <div className="text-[11px] text-red-400 break-words">{ev.action_error}</div>
                        </div>
                      )}
                      <EventNotesPanel eventId={ev.id} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
