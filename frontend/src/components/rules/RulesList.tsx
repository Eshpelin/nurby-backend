"use client";

import { RuleCard } from "./RuleCard";
import type { Camera, Rule } from "./types";

export interface RulesListProps {
  rules: Rule[];
  cameras: Camera[];
  selectedRuleId: string | null;
  onSelect: (rule: Rule) => void;
  onToggleEnabled: (rule: Rule) => void;
  onEdit: (rule: Rule) => void;
  onDelete: (ruleId: string) => void;
}

export function RulesList({
  rules,
  cameras,
  selectedRuleId,
  onSelect,
  onToggleEnabled,
  onEdit,
  onDelete,
}: RulesListProps) {
  return (
    <section className="col-span-8 space-y-3">
      {rules.map((r) => (
        <RuleCard
          key={r.id}
          rule={r}
          cameras={cameras}
          selected={selectedRuleId === r.id}
          onSelect={() => onSelect(r)}
          onToggleEnabled={() => onToggleEnabled(r)}
          onEdit={() => onEdit(r)}
          onDelete={() => onDelete(r.id)}
        />
      ))}
    </section>
  );
}
