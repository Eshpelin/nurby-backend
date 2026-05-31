"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  cameraLookup,
  personLookup,
  type Camera,
  type EventEntry,
  type Person,
  type Rule,
  type TelegramChannelOption,
} from "@/components/rules/types";
import { RulesList } from "@/components/rules/RulesList";
import { RuleEventsPanel } from "@/components/rules/RuleEventsPanel";
import { WebhookSubscriptions } from "@/components/rules/WebhookSubscriptions";
import { RULE_PREFILL_KEY } from "@/app/rules/new/page";

const LAST_FIRED_CACHE_MS = 30_000;

export default function RulesPage() {
  const { authFetch } = useAuth();
  const router = useRouter();
  const [rules, setRules] = useState<Rule[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);

  const [telegramChannels, setTelegramChannels] = useState<TelegramChannelOption[]>([]);
  const [telegramChannelsLoading, setTelegramChannelsLoading] = useState(false);

  // Most-recent event timestamp per rule. Computed from a single
  // /api/events fetch on mount + after each save. Cached for 30s.
  const [lastFiredByRule, setLastFiredByRule] = useState<Record<string, string | null>>({});
  const lastFiredFetchedAt = useRef<number>(0);

  const fetchRules = useCallback(async () => {
    try {
      const res = await authFetch("/api/rules");
      if (res.ok) setRules(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) {
        const list = await res.json();
        setCameras(list);
        cameraLookup.clear();
        for (const c of list) cameraLookup.set(c.id, c.name);
      }
    } catch {
      /* silent */
    }
  }, [authFetch]);

  const fetchPersons = useCallback(async () => {
    try {
      const res = await authFetch("/api/persons");
      if (res.ok) {
        const list = await res.json();
        setPersons(list);
        personLookup.clear();
        for (const p of list) personLookup.set(p.id, p.display_name);
      }
    } catch {
      /* silent */
    }
  }, [authFetch]);

  const fetchTelegramChannels = useCallback(async () => {
    setTelegramChannelsLoading(true);
    try {
      const res = await authFetch("/api/telegram/channels");
      if (res.ok) {
        const list: TelegramChannelOption[] = await res.json();
        setTelegramChannels(list);
      }
    } catch {
      /* silent */
    } finally {
      setTelegramChannelsLoading(false);
    }
  }, [authFetch]);

  // Aggregate "last fired" timestamps from the events feed. The
  // backend exposes no per-rule last_fired_at field today, so we
  // reduce the recent event history client-side. Refreshed on save.
  const fetchLastFired = useCallback(async (force = false) => {
    const now = Date.now();
    if (!force && now - lastFiredFetchedAt.current < LAST_FIRED_CACHE_MS) return;
    lastFiredFetchedAt.current = now;
    try {
      const res = await authFetch("/api/events?limit=200");
      if (!res.ok) return;
      const list = (await res.json()) as EventEntry[];
      const map: Record<string, string | null> = {};
      for (const e of list) {
        if (!e.rule_id) continue;
        const existing = map[e.rule_id];
        if (!existing || new Date(e.fired_at) > new Date(existing)) {
          map[e.rule_id] = e.fired_at;
        }
      }
      setLastFiredByRule(map);
    } catch {
      /* silent */
    }
  }, [authFetch]);

  useEffect(() => {
    fetchRules();
    fetchCameras();
    fetchPersons();
    fetchTelegramChannels();
    fetchLastFired(true);
  }, [fetchRules, fetchCameras, fetchPersons, fetchTelegramChannels, fetchLastFired]);

  const stashPrefillAndCreate = (synth: Rule) => {
    try {
      sessionStorage.setItem(RULE_PREFILL_KEY, JSON.stringify(synth));
    } catch {
      /* ignore quota errors. the create page just opens blank */
    }
    router.push("/rules/new");
  };

  const openCreate = () => router.push("/rules/new");

  const openEdit = (r: Rule) => router.push(`/rules/${r.id}/edit`);

  const openDuplicate = (r: Rule) => {
    // Clone, suffix name, force disabled so the copy doesn't fire
    // silently. Empty id means the create page POSTs a new rule.
    stashPrefillAndCreate({ ...r, id: "", name: `${r.name} (copy)`, enabled: false });
  };

  const openPersona = (synth: Rule) => stashPrefillAndCreate(synth);

  const handleDelete = async (id: string) => {
    try {
      await authFetch(`/api/rules/${id}`, { method: "DELETE" });
      if (selectedRule?.id === id) setSelectedRule(null);
      fetchRules();
    } catch {
      /* silent */
    }
  };

  const handleToggle = async (rule: Rule) => {
    // Optimistic update so the card switches visually before the
    // round-trip lands.
    setRules((prev) =>
      prev.map((r) => (r.id === rule.id ? { ...r, enabled: !rule.enabled } : r)),
    );
    try {
      await authFetch(`/api/rules/${rule.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...rule, enabled: !rule.enabled }),
      });
      fetchRules();
    } catch {
      /* silent. revert handled on next fetchRules */
      fetchRules();
    }
  };

  const ruleCount = useMemo(() => rules.length, [rules]);

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {ruleCount} rule{ruleCount !== 1 ? "s" : ""} configured
          </p>
        </div>
        <button
          onClick={openCreate}
          className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
        >
          + Create rule
        </button>
      </div>

      {loading ? (
        <div className="text-sm text-muted-foreground py-20 text-center">
          Loading.
        </div>
      ) : (
        <div className="grid grid-cols-12 gap-6">
          <RulesList
            rules={rules}
            cameras={cameras}
            selectedRuleId={selectedRule?.id ?? null}
            lastFiredByRule={lastFiredByRule}
            telegramChannels={telegramChannels}
            onSelect={setSelectedRule}
            onToggleEnabled={handleToggle}
            onEdit={openEdit}
            onDuplicate={openDuplicate}
            onDelete={handleDelete}
            onPrefillFromPersona={openPersona}
            onCreateBlank={openCreate}
          />
          {rules.length > 0 && (
            <RuleEventsPanel selectedRule={selectedRule} cameras={cameras} />
          )}
        </div>
      )}

      <WebhookSubscriptions />
    </div>
  );
}
