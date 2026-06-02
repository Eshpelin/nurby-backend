"use client";

import { RuleCard } from "./RuleCard";
import { type Camera, type Rule, type TelegramChannelOption } from "./types";

export interface RulesListProps {
  rules: Rule[];
  cameras: Camera[];
  selectedRuleId: string | null;
  lastFiredByRule: Record<string, string | null>;
  telegramChannels: TelegramChannelOption[];
  onSelect: (rule: Rule) => void;
  onToggleEnabled: (rule: Rule) => void;
  onEdit: (rule: Rule) => void;
  onDuplicate: (rule: Rule) => void;
  onDelete: (ruleId: string) => void;
  // Triggered by empty-state UX. callers open the modal with the
  // synthesized prefill rule.
  onPrefillFromPersona: (rule: Rule) => void;
  onCreateBlank: () => void;
}

// ── Persona prefills for the empty state ──
//
// Each persona builds a synthetic Rule whose shape matches what
// RuleModal.hydrate / ruleFormReducer.hydrate expect. The id is "" so
// the modal treats this as a NEW rule (POST on save), not an edit.

interface Persona {
  key: string;
  emoji: string;
  title: string;
  blurb: string;
  build: (ctx: { cameras: Camera[]; telegramChannels: TelegramChannelOption[] }) => Rule;
}

function synthRule(
  name: string,
  trigger_pattern: Record<string, unknown>,
  actions: Record<string, unknown>[],
  conditions: Record<string, unknown> | null = null,
  cooldown_seconds = 300,
): Rule {
  return {
    id: "",
    name,
    enabled: true,
    trigger_pattern,
    conditions,
    actions,
    cooldown_seconds,
    created_at: new Date().toISOString(),
  };
}

const PERSONAS: Persona[] = [
  {
    key: "package",
    emoji: "📦",
    title: "Tell me when a package arrives at the front door",
    blurb: "object_detected (package) → Telegram or notification",
    build: ({ cameras, telegramChannels }) => {
      const frontDoorCam = cameras.find((c) => /front\s*door/i.test(c.name));
      const paired = telegramChannels.find(
        (c) => c.enabled && c.pairing_status === "paired",
      );
      // Emit the dict form expected by hydrateFromRule. DictToDraft
      // maps it into an ActionDraft for the chain editor.
      const dict: Record<string, unknown> = paired
        ? {
            type: "telegram",
            channel_id: paired.id,
            template: "📦 Package at {camera_name} ({timestamp_local})",
            silent: false,
            include_thumbnail: true,
          }
        : { type: "notify", message: "Package detected at front door", severity: "info" };
      return synthRule(
        "Package at front door",
        { type: "object_detected", label: "package" },
        [dict],
        frontDoorCam ? { camera_ids: [frontDoorCam.id] } : null,
      );
    },
  },
  {
    key: "intruder",
    emoji: "🚨",
    title: "Email me if an unknown face shows up at night",
    blurb: "face_unknown + time window → email",
    build: () =>
      synthRule(
        "Unknown face at night",
        { type: "face_unknown" },
        [
          {
            type: "email",
            to: "",
            subject: "Nurby. Unknown face spotted",
            body:
              "An unknown face was detected at {{timestamp}} on camera {{camera_id}}.",
          },
        ],
        { time_after: "22:00", time_before: "06:00" },
      ),
  },
  {
    key: "babycry",
    emoji: "🍼",
    title: "Webhook on baby cry",
    blurb: "audio_event (baby_cry) → webhook",
    build: () =>
      synthRule(
        "Baby cry webhook",
        { type: "audio_event", label: "baby_cry", min_score: 0.35 },
        [
          {
            type: "webhook",
            url: "https://example.com/baby-cry",
          },
        ],
        null,
        60,
      ),
  },
  {
    key: "dogwalk",
    emoji: "🐕",
    title: "Recap dog walks on Telegram",
    blurb: "face_recognized → Telegram (pick the person)",
    build: ({ telegramChannels }) => {
      const paired = telegramChannels.find(
        (c) => c.enabled && c.pairing_status === "paired",
      );
      return synthRule(
        "Dog walk recap",
        { type: "face_recognized" },
        [
          paired
            ? {
                type: "telegram",
                channel_id: paired.id,
                template: "🐕 {rule_name} at {timestamp_local}",
                silent: false,
                include_thumbnail: false,
              }
            : { type: "notify", message: "Dog walker home", severity: "info" },
        ],
      );
    },
  },
];

function EmptyState({
  cameras,
  telegramChannels,
  onPrefillFromPersona,
  onCreateBlank,
}: {
  cameras: Camera[];
  telegramChannels: TelegramChannelOption[];
  onPrefillFromPersona: (rule: Rule) => void;
  onCreateBlank: () => void;
}) {
  return (
    <div className="col-span-12 py-10">
      <div className="text-center mb-6">
        <h2 className="text-lg font-semibold">Start from a template</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Pick a recipe to prefill the rule modal. Tweak anything before you save.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-3xl mx-auto">
        {PERSONAS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => onPrefillFromPersona(p.build({ cameras, telegramChannels }))}
            className="text-left rounded-lg border border-border bg-card p-4 hover:border-accent transition-colors"
          >
            <div className="text-2xl mb-1">{p.emoji}</div>
            <div className="font-medium text-sm">{p.title}</div>
            <div className="text-[11px] text-muted-foreground mt-1">{p.blurb}</div>
          </button>
        ))}
      </div>
      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={onCreateBlank}
          className="text-xs text-muted-foreground hover:text-foreground underline"
        >
          Or start from scratch
        </button>
      </div>
    </div>
  );
}

export function RulesList({
  rules,
  cameras,
  selectedRuleId,
  lastFiredByRule,
  telegramChannels,
  onSelect,
  onToggleEnabled,
  onEdit,
  onDuplicate,
  onDelete,
  onPrefillFromPersona,
  onCreateBlank,
}: RulesListProps) {
  if (rules.length === 0) {
    return (
      <EmptyState
        cameras={cameras}
        telegramChannels={telegramChannels}
        onPrefillFromPersona={onPrefillFromPersona}
        onCreateBlank={onCreateBlank}
      />
    );
  }
  return (
    <section className="col-span-8 space-y-3">
      {rules.map((r) => (
        <RuleCard
          key={r.id}
          rule={r}
          cameras={cameras}
          selected={selectedRuleId === r.id}
          lastFiredAt={lastFiredByRule[r.id] ?? null}
          onSelect={() => onSelect(r)}
          onToggleEnabled={() => onToggleEnabled(r)}
          onEdit={() => onEdit(r)}
          onDuplicate={() => onDuplicate(r)}
          onDelete={() => onDelete(r.id)}
        />
      ))}
    </section>
  );
}
