// Reducer for the rule create/edit modal. Centralises every form
// field so trigger-type switches wipe stale state instead of leaking
// fields from a prior choice. The action chain lives as
// `formActions: ActionDraft[]`; ActionsSection owns per-card editing.
import {
  defaultDraftForType,
  dictToDraft,
  type ActionDraft,
  type Rule,
} from "./types";

export interface RuleFormState {
  // Identity
  formName: string;
  formEnabled: boolean;

  // Trigger group
  formTriggerType: string;
  formTriggerLabel: string;
  formTriggerPersonId: string;
  formTriggerSensitivity: string;
  formTriggerAudioLabel: string;
  formTriggerAudioMinScore: string;
  formTriggerLineDirection: string;
  formTriggerGeomCamId: string;
  formTriggerGeomPoints: number[][];
  formTriggerLoiterSeconds: string;
  formTriggerObjectClass: string;
  formTriggerClapCount: string;
  formTriggerPhrases: string[];
  formTriggerPhraseMatch: "any" | "all";

  // Conditions group
  formCondCameras: string[];
  formScheduleMode: "always" | "custom";
  formCondDays: string[];
  formCondTimeAfter: string;
  formCondTimeBefore: string;
  formCondConfidence: string;

  // Action chain.
  formActions: ActionDraft[];

  // Misc
  formCooldown: string;
  formError: string;
  submitting: boolean;
}

export const INITIAL_RULE_FORM_STATE: RuleFormState = {
  formName: "",
  formEnabled: true,

  formTriggerType: "object_detected",
  formTriggerLabel: "",
  formTriggerPersonId: "",
  formTriggerSensitivity: "medium",
  formTriggerAudioLabel: "baby_cry",
  formTriggerAudioMinScore: "0.35",
  formTriggerLineDirection: "any",
  formTriggerGeomCamId: "",
  formTriggerGeomPoints: [],
  formTriggerLoiterSeconds: "30",
  formTriggerObjectClass: "",
  formTriggerClapCount: "2",
  formTriggerPhrases: [],
  formTriggerPhraseMatch: "any",

  formCondCameras: [],
  formScheduleMode: "always",
  formCondDays: [],
  formCondTimeAfter: "",
  formCondTimeBefore: "",
  formCondConfidence: "any",

  formActions: [defaultDraftForType("notify")],

  formCooldown: "300",
  formError: "",
  submitting: false,
};

// Trigger fields wiped on trigger-type switch. Identity, conditions,
// action chain and cooldown are preserved across switches.
const TRIGGER_FIELDS_TO_RESET: (keyof RuleFormState)[] = [
  "formTriggerLabel",
  "formTriggerPersonId",
  "formTriggerSensitivity",
  "formTriggerAudioLabel",
  "formTriggerAudioMinScore",
  "formTriggerLineDirection",
  "formTriggerGeomCamId",
  "formTriggerGeomPoints",
  "formTriggerLoiterSeconds",
  "formTriggerObjectClass",
  "formTriggerClapCount",
  "formTriggerPhrases",
  "formTriggerPhraseMatch",
];

export type RuleFormAction =
  | { type: "reset" }
  | { type: "hydrate"; rule: Rule }
  | { type: "setField"; field: keyof RuleFormState; value: unknown }
  | { type: "setTriggerType"; value: string }
  // Single-action compatibility shim. Replaces the chain with one
  // fresh draft. ActionsSection drives per-card type switches via
  // setFormActions and does not dispatch this.
  | { type: "setActionType"; value: string }
  | { type: "setFormActions"; value: ActionDraft[] }
  | { type: "setTriggerGeomPoints"; value: number[][] }
  | { type: "setTriggerPhrases"; value: string[] }
  | { type: "setError"; value: string }
  | { type: "setSubmitting"; value: boolean };

function resetFields(
  state: RuleFormState,
  fields: (keyof RuleFormState)[],
): RuleFormState {
  const next: RuleFormState = { ...state };
  for (const f of fields) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (next as any)[f] = (INITIAL_RULE_FORM_STATE as any)[f];
  }
  return next;
}

export function hydrateFromRule(rule: Rule): RuleFormState {
  const r = rule;
  const tp = r.trigger_pattern;
  const cond = r.conditions || {};
  const rawActions = Array.isArray(r.actions) ? r.actions : [r.actions];

  const base: RuleFormState = { ...INITIAL_RULE_FORM_STATE };

  base.formName = r.name;
  base.formEnabled = r.enabled;

  base.formTriggerType = (tp.type as string) || "any";
  // formTriggerLabel doubles as the object label and the vehicle plate text.
  base.formTriggerLabel = (tp.label as string) || (tp.plate as string) || "";
  base.formTriggerPersonId = (tp.person_id as string) || "";
  base.formTriggerAudioLabel = (tp.label as string) || "baby_cry";
  base.formTriggerAudioMinScore =
    tp.min_score != null ? String(tp.min_score) : "0.35";
  base.formTriggerLineDirection = (tp.direction as string) || "any";
  base.formTriggerGeomCamId = (tp.camera_id as string) || "";
  const pts = tp.points as number[][] | undefined;
  base.formTriggerGeomPoints = Array.isArray(pts) ? pts : [];
  base.formTriggerLoiterSeconds =
    tp.threshold_seconds != null ? String(tp.threshold_seconds) : "30";
  base.formTriggerObjectClass = (tp.label as string) || "";
  base.formTriggerClapCount = tp.count != null ? String(tp.count) : "2";
  base.formTriggerPhrases = Array.isArray(tp.phrases)
    ? (tp.phrases as string[])
    : [];
  base.formTriggerPhraseMatch =
    (tp.match as "any" | "all") === "all" ? "all" : "any";
  const ms = tp.min_score as number | undefined;
  if (ms != null) {
    if (ms <= 0.02) base.formTriggerSensitivity = "very_high";
    else if (ms <= 0.05) base.formTriggerSensitivity = "high";
    else if (ms <= 0.15) base.formTriggerSensitivity = "medium";
    else base.formTriggerSensitivity = "low";
  } else {
    base.formTriggerSensitivity = "medium";
  }

  const camIds = cond.camera_ids as string[] | undefined;
  const camId = cond.camera_id as string | undefined;
  base.formCondCameras = camIds || (camId ? [camId] : []);
  const days = cond.days as string[] | undefined;
  base.formCondDays = days || [];
  const hasSchedule = !!(
    cond.time_after ||
    cond.time_before ||
    (days && days.length > 0)
  );
  base.formScheduleMode = hasSchedule ? "custom" : "always";
  base.formCondTimeAfter = (cond.time_after as string) || "";
  base.formCondTimeBefore = (cond.time_before as string) || "";
  const mc = cond.min_confidence as number | undefined;
  if (mc != null) {
    if (mc >= 0.8) base.formCondConfidence = "very_high";
    else if (mc >= 0.6) base.formCondConfidence = "high";
    else if (mc >= 0.3) base.formCondConfidence = "medium";
    else base.formCondConfidence = "low";
  } else {
    base.formCondConfidence = "any";
  }

  base.formActions =
    rawActions.length > 0
      ? rawActions.map((a) => dictToDraft(a as Record<string, unknown>))
      : [defaultDraftForType("notify")];

  base.formCooldown = String(r.cooldown_seconds);
  base.formError = "";
  base.submitting = false;

  return base;
}

export function ruleFormReducer(
  state: RuleFormState,
  action: RuleFormAction,
): RuleFormState {
  switch (action.type) {
    case "reset":
      return {
        ...INITIAL_RULE_FORM_STATE,
        formActions: [defaultDraftForType("notify")],
      };
    case "hydrate":
      return hydrateFromRule(action.rule);
    case "setField":
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return { ...state, [action.field]: action.value as any };
    case "setTriggerType": {
      const cleared = resetFields(state, TRIGGER_FIELDS_TO_RESET);
      return { ...cleared, formTriggerType: action.value };
    }
    case "setActionType": {
      const t = action.value as Parameters<typeof defaultDraftForType>[0];
      return { ...state, formActions: [defaultDraftForType(t)] };
    }
    case "setFormActions":
      return { ...state, formActions: action.value };
    case "setTriggerGeomPoints":
      return { ...state, formTriggerGeomPoints: action.value };
    case "setTriggerPhrases":
      return { ...state, formTriggerPhrases: action.value };
    case "setError":
      return { ...state, formError: action.value };
    case "setSubmitting":
      return { ...state, submitting: action.value };
    default:
      return state;
  }
}
