"use client";

import {
  defaultDraftForType,
  availableVarsBefore,
  MAX_ACTIONS_PER_RULE,
  type ActionType,
  type ActionDraft,
  type TelegramChannelOption,
} from "./types";
import { ActionCard } from "./actions/ActionCard";
import { useState } from "react";

export interface ActionsSectionProps {
  telegramChannels: TelegramChannelOption[];
  telegramChannelsLoading: boolean;

  formActions: ActionDraft[];
  setFormActions: (updater: ActionDraft[] | ((prev: ActionDraft[]) => ActionDraft[])) => void;

  // Per-card error message keyed by card index (var-ref validation).
  cardErrors: Record<number, string>;
}

export function ActionsSection(props: ActionsSectionProps) {
  const { telegramChannels, telegramChannelsLoading, formActions, setFormActions, cardErrors } =
    props;
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});

  const replaceAt = (i: number, next: ActionDraft) => {
    setFormActions((prev) => prev.map((d, idx) => (idx === i ? next : d)));
  };
  const moveAt = (i: number, delta: -1 | 1) => {
    setFormActions((prev) => {
      const j = i + delta;
      if (j < 0 || j >= prev.length) return prev;
      const next = prev.slice();
      const tmp = next[i];
      next[i] = next[j];
      next[j] = tmp;
      return next;
    });
  };
  const deleteAt = (i: number) => {
    setFormActions((prev) => (prev.length <= 1 ? prev : prev.filter((_, idx) => idx !== i)));
  };
  const changeTypeAt = (i: number, t: ActionType) => {
    setFormActions((prev) =>
      prev.map((d, idx) => (idx === i ? defaultDraftForType(t) : d)),
    );
  };
  const addAction = () => {
    setFormActions((prev) =>
      prev.length >= MAX_ACTIONS_PER_RULE
        ? prev
        : [...prev, defaultDraftForType("notify")],
    );
  };

  return (
    <fieldset className="border border-border rounded-md p-3 space-y-3">
      <legend className="text-xs font-medium text-muted-foreground px-1">
        Actions ({formActions.length})
      </legend>
      {formActions.map((draft, i) => (
        <ActionCard
          key={i}
          index={i}
          draft={draft}
          totalCount={formActions.length}
          onReplace={(next) => replaceAt(i, next)}
          onTypeChange={(t) => changeTypeAt(i, t)}
          onMove={(delta) => moveAt(i, delta)}
          onRemove={() => deleteAt(i)}
          errorMessage={cardErrors[i]}
          availableVars={availableVarsBefore(formActions, i)}
          telegramChannels={telegramChannels}
          telegramChannelsLoading={telegramChannelsLoading}
          isCollapsed={!!collapsed[i]}
          onToggleCollapsed={() =>
            setCollapsed((m) => ({ ...m, [i]: !m[i] }))
          }
        />
      ))}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={addAction}
          disabled={formActions.length >= MAX_ACTIONS_PER_RULE}
          className="px-2 py-1 text-xs rounded border border-dashed border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
        >
          + Add action
        </button>
        {formActions.length >= MAX_ACTIONS_PER_RULE && (
          <span className="text-[10px] text-muted-foreground">
            Limit of {MAX_ACTIONS_PER_RULE} actions reached.
          </span>
        )}
      </div>
    </fieldset>
  );
}
