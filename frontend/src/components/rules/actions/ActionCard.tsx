"use client";

import {
  ACTION_TYPES,
  type ActionDraft,
  type ActionType,
  type WebhookDraft,
  type BroadcastDraft,
  type NotifyDraft,
  type EmailDraft,
  type TelegramDraft,
  type VlmCallDraft,
  type VerifyDraft,
  type TelegramChannelOption,
} from "../types";
import { StyledSelect } from "../StyledSelect";
import { WebhookEditor } from "./WebhookEditor";
import { BroadcastEditor } from "./BroadcastEditor";
import { NotifyEditor } from "./NotifyEditor";
import { EmailEditor } from "./EmailEditor";
import { TelegramEditor } from "./TelegramEditor";
import { VlmCallEditor } from "./VlmCallEditor";
import { VerifyEditor } from "./VerifyEditor";
import { type VarSpec } from "./VarInserter";

export interface ActionCardProps {
  index: number;
  draft: ActionDraft;
  totalCount: number;
  onReplace: (next: ActionDraft) => void;
  onTypeChange: (type: ActionType) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  errorMessage?: string;
  availableVars: VarSpec[];
  telegramChannels: TelegramChannelOption[];
  telegramChannelsLoading: boolean;
  isCollapsed: boolean;
  onToggleCollapsed: () => void;
}

export function ActionCard({
  index,
  draft,
  totalCount,
  onReplace,
  onTypeChange,
  onMove,
  onRemove,
  errorMessage,
  availableVars,
  telegramChannels,
  telegramChannelsLoading,
  isCollapsed,
  onToggleCollapsed,
}: ActionCardProps) {
  const typeLabel =
    ACTION_TYPES.find((a) => a.value === draft.type)?.label || draft.type;
  return (
    <fieldset
      className={`border rounded-md p-3 space-y-3 ${
        errorMessage ? "border-red-500/60" : "border-border"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="px-1.5 py-0.5 text-[10px] rounded bg-muted text-zinc-300 font-mono">
            {index + 1}
          </span>
          <span className="text-xs px-1.5 py-0.5 rounded border border-border text-muted-foreground">
            {typeLabel}
          </span>
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground"
            title={isCollapsed ? "Expand" : "Collapse"}
          >
            {isCollapsed ? "▸" : "▾"}
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={index === 0}
            onClick={() => onMove(-1)}
            className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-30"
            title="Move up"
          >
            ↑
          </button>
          <button
            type="button"
            disabled={index === totalCount - 1}
            onClick={() => onMove(1)}
            className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-30"
            title="Move down"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={onRemove}
            className="text-[10px] px-1.5 py-0.5 rounded border border-red-800 text-red-400 hover:bg-red-900/30"
            title="Delete action"
          >
            ✕
          </button>
        </div>
      </div>
      {!isCollapsed && (
        <>
          <StyledSelect
            value={draft.type}
            options={ACTION_TYPES.map((a) => ({ value: a.value, label: a.label }))}
            onChange={(v) => onTypeChange(v as ActionType)}
          />
          {(draft.type === "webhook" || draft.type === "api_call") && (
            <WebhookEditor
              draft={draft as WebhookDraft}
              onChange={(next) => onReplace(next)}
              availableVars={availableVars}
            />
          )}
          {draft.type === "broadcast" && (
            <BroadcastEditor
              draft={draft as BroadcastDraft}
              onChange={(next) => onReplace(next)}
              availableVars={availableVars}
            />
          )}
          {draft.type === "notify" && (
            <NotifyEditor
              draft={draft as NotifyDraft}
              onChange={(next) => onReplace(next)}
              availableVars={availableVars}
            />
          )}
          {draft.type === "email" && (
            <EmailEditor
              draft={draft as EmailDraft}
              onChange={(next) => onReplace(next)}
              availableVars={availableVars}
            />
          )}
          {draft.type === "telegram" && (
            <TelegramEditor
              draft={draft as TelegramDraft}
              onChange={(next) => onReplace(next)}
              availableVars={availableVars}
              telegramChannels={telegramChannels}
              telegramChannelsLoading={telegramChannelsLoading}
            />
          )}
          {draft.type === "vlm_call" && (
            <VlmCallEditor
              draft={draft as VlmCallDraft}
              onChange={(next) => onReplace(next)}
            />
          )}
          {draft.type === "verify" && (
            <VerifyEditor
              draft={draft as VerifyDraft}
              onChange={(next) => onReplace(next)}
            />
          )}
        </>
      )}
      {errorMessage && (
        <div className="text-[11px] text-red-400">{errorMessage}</div>
      )}
    </fieldset>
  );
}

export default ActionCard;
