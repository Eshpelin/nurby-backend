"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  cameraLookup,
  personLookup,
  type Camera,
  type Person,
  type Rule,
  type TelegramChannelOption,
} from "@/components/rules/types";
import { RulesList } from "@/components/rules/RulesList";
import { RuleEventsPanel } from "@/components/rules/RuleEventsPanel";
import { RuleModal } from "@/components/rules/RuleModal";

export default function RulesPage() {
  const { authFetch } = useAuth();
  const [rules, setRules] = useState<Rule[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editRule, setEditRule] = useState<Rule | null>(null);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);

  const [telegramChannels, setTelegramChannels] = useState<TelegramChannelOption[]>([]);
  const [telegramChannelsLoading, setTelegramChannelsLoading] = useState(false);

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

  useEffect(() => {
    fetchRules();
    fetchCameras();
    fetchPersons();
    fetchTelegramChannels();
  }, [fetchRules, fetchCameras, fetchPersons, fetchTelegramChannels]);

  const openCreate = () => {
    setEditRule(null);
    setShowModal(true);
  };

  const openEdit = (r: Rule) => {
    setEditRule(r);
    setShowModal(true);
  };

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
    try {
      await authFetch(`/api/rules/${rule.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...rule, enabled: !rule.enabled }),
      });
      fetchRules();
    } catch {
      /* silent */
    }
  };

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {rules.length} rule{rules.length !== 1 ? "s" : ""} configured
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
      ) : rules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full border border-border flex items-center justify-center mb-4 text-muted-foreground text-2xl">
            ?
          </div>
          <p className="text-muted-foreground text-sm mb-4">
            No rules created yet. Rules let you define triggers, conditions,
            and actions to automate your monitoring.
          </p>
          <button
            onClick={openCreate}
            className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90"
          >
            + Create first rule
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-12 gap-6">
          <RulesList
            rules={rules}
            cameras={cameras}
            selectedRuleId={selectedRule?.id ?? null}
            onSelect={setSelectedRule}
            onToggleEnabled={handleToggle}
            onEdit={openEdit}
            onDelete={handleDelete}
          />
          <RuleEventsPanel selectedRule={selectedRule} cameras={cameras} />
        </div>
      )}

      <RuleModal
        open={showModal}
        onClose={() => setShowModal(false)}
        editRule={editRule}
        cameras={cameras}
        persons={persons}
        telegramChannels={telegramChannels}
        telegramChannelsLoading={telegramChannelsLoading}
        onSaved={fetchRules}
      />
    </div>
  );
}
