"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  composeSummary,
  describeActions,
  describeSchedule,
  describeTrigger,
  resolveCameraNames,
  TELEGRAM_DEFAULT_BUTTONS,
  TELEGRAM_DEFAULT_TEMPLATE,
  VLM_SCHEMA_PRESETS,
  isValidHttpUrlOrTemplate,
  type Camera,
  type Person,
  type Rule,
  type TelegramButton,
  type TelegramChannelOption,
} from "./types";
import { SummaryCard } from "./SummaryCard";
import { TriggerSection } from "./TriggerSection";
import { ConditionsSection } from "./ConditionsSection";
import { ActionsSection } from "./ActionsSection";

export interface RuleModalProps {
  open: boolean;
  onClose: () => void;
  editRule: Rule | null;
  cameras: Camera[];
  persons: Person[];
  telegramChannels: TelegramChannelOption[];
  telegramChannelsLoading: boolean;
  onSaved: () => void;
}

const DEFAULT_TELEGRAM_TEMPLATE_FALLBACK =
  "<b>{rule_name}</b> on {camera_name}\n{vlm_description}\n{detections_summary}";

export function RuleModal({
  open,
  onClose,
  editRule,
  cameras,
  persons,
  telegramChannels,
  telegramChannelsLoading,
  onSaved,
}: RuleModalProps) {
  const { authFetch } = useAuth();

  // Form state. Lifted verbatim from RulesPage.
  const [formName, setFormName] = useState("");
  const [formEnabled, setFormEnabled] = useState(true);
  const [formTriggerType, setFormTriggerType] = useState("object_detected");
  const [formTriggerLabel, setFormTriggerLabel] = useState("");
  const [formTriggerPersonId, setFormTriggerPersonId] = useState("");
  const [formTriggerSensitivity, setFormTriggerSensitivity] = useState("medium");
  const [formTriggerAudioLabel, setFormTriggerAudioLabel] = useState("baby_cry");
  const [formTriggerAudioMinScore, setFormTriggerAudioMinScore] = useState("0.35");
  const [formTriggerLineDirection, setFormTriggerLineDirection] = useState("any");
  const [formTriggerGeomCamId, setFormTriggerGeomCamId] = useState("");
  const [formTriggerGeomPoints, setFormTriggerGeomPoints] = useState<number[][]>([]);
  const [formTriggerLoiterSeconds, setFormTriggerLoiterSeconds] = useState("30");
  const [formTriggerObjectClass, setFormTriggerObjectClass] = useState("");
  const [formTriggerClapCount, setFormTriggerClapCount] = useState("2");
  const [formTriggerPhrases, setFormTriggerPhrases] = useState<string[]>([]);
  const [formTriggerPhraseMatch, setFormTriggerPhraseMatch] = useState<"any" | "all">("any");
  const [formCondCameras, setFormCondCameras] = useState<string[]>([]);
  const [formScheduleMode, setFormScheduleMode] = useState<"always" | "custom">("always");
  const [formCondDays, setFormCondDays] = useState<string[]>([]);
  const [formCondTimeAfter, setFormCondTimeAfter] = useState("");
  const [formCondTimeBefore, setFormCondTimeBefore] = useState("");
  const [formCondConfidence, setFormCondConfidence] = useState("any");
  const [formActionType, setFormActionType] = useState("notify");
  const [formActionUrl, setFormActionUrl] = useState("");
  const [formActionMethod, setFormActionMethod] = useState("POST");
  const [formActionMessage, setFormActionMessage] = useState("");
  const [formActionSeverity, setFormActionSeverity] = useState("info");
  const [formActionAuthType, setFormActionAuthType] = useState("none");
  const [formActionAuthToken, setFormActionAuthToken] = useState("");
  const [formActionAuthHeader, setFormActionAuthHeader] = useState("X-API-Key");
  const [formActionAuthKey, setFormActionAuthKey] = useState("");
  const [formActionAuthUser, setFormActionAuthUser] = useState("");
  const [formActionAuthPass, setFormActionAuthPass] = useState("");
  const [formActionPayloadTemplate, setFormActionPayloadTemplate] = useState("");
  const [formActionUseCustomPayload, setFormActionUseCustomPayload] = useState(false);
  const [formPayloadError, setFormPayloadError] = useState("");
  const [formActionEmailTo, setFormActionEmailTo] = useState("");
  const [formActionEmailSubject, setFormActionEmailSubject] = useState("");
  const [formActionEmailBody, setFormActionEmailBody] = useState("");
  const [formActionTelegramChannelId, setFormActionTelegramChannelId] = useState("");
  const [formActionTelegramTemplate, setFormActionTelegramTemplate] = useState(
    DEFAULT_TELEGRAM_TEMPLATE_FALLBACK,
  );
  const [formActionTelegramSilent, setFormActionTelegramSilent] = useState(false);
  const [formActionTelegramThumbnail, setFormActionTelegramThumbnail] = useState(false);
  const [formActionTelegramButtons, setFormActionTelegramButtons] = useState<TelegramButton[]>(
    TELEGRAM_DEFAULT_BUTTONS,
  );
  const [telegramDefaultsApplied, setTelegramDefaultsApplied] = useState(false);
  const [formVlmProvider, setFormVlmProvider] = useState("openai");
  const [formVlmModel, setFormVlmModel] = useState("gpt-4o-mini");
  const [formVlmSystem, setFormVlmSystem] = useState("{{defaults.system}}");
  const [formVlmPrompt, setFormVlmPrompt] = useState(
    "Describe the scene. Focus on people, vehicles, and unusual activity.",
  );
  const [formVlmAttachImage, setFormVlmAttachImage] = useState(true);
  const [formVlmUseSchema, setFormVlmUseSchema] = useState(false);
  const [formVlmSchemaText, setFormVlmSchemaText] = useState(VLM_SCHEMA_PRESETS.threat);
  const [formVlmOutput, setFormVlmOutput] = useState("result");
  const [formVlmMaxRetries, setFormVlmMaxRetries] = useState("1");
  const [formVlmOnError, setFormVlmOnError] = useState("continue");
  const [formVlmTimeoutMs, setFormVlmTimeoutMs] = useState("20000");
  const [formCooldown, setFormCooldown] = useState("300");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Dynamic class vocabulary from configured detection models.
  const [modelClasses, setModelClasses] = useState<string[]>([]);
  const [modelClassesLoading, setModelClassesLoading] = useState(false);
  const activeModels = useMemo(() => {
    const scoped = formCondCameras.length > 0
      ? cameras.filter((c) => formCondCameras.includes(c.id))
      : cameras;
    const set = new Set<string>();
    for (const c of scoped) {
      for (const m of c.detection_models || []) {
        if (m?.model && m.enabled !== false) set.add(m.model);
      }
    }
    return Array.from(set).sort();
  }, [cameras, formCondCameras]);

  useEffect(() => {
    if (activeModels.length === 0) {
      setModelClasses([]);
      return;
    }
    let cancelled = false;
    setModelClassesLoading(true);
    const params = activeModels.map((m) => `model=${encodeURIComponent(m)}`).join("&");
    authFetch(`/api/detection-models/classes?${params}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data?.classes) setModelClasses(data.classes);
      })
      .catch(() => { /* silent */ })
      .finally(() => { if (!cancelled) setModelClassesLoading(false); });
    return () => { cancelled = true; };
  }, [activeModels, authFetch]);

  // Prefill Phase 2 telegram defaults the first time the user picks
  // telegram. Editing preserves what the rule already had.
  useEffect(() => {
    if (
      formActionType === "telegram" &&
      !telegramDefaultsApplied &&
      !editRule &&
      formActionTelegramButtons.length === 0
    ) {
      setFormActionTelegramButtons(TELEGRAM_DEFAULT_BUTTONS);
      setFormActionTelegramTemplate(TELEGRAM_DEFAULT_TEMPLATE);
      setTelegramDefaultsApplied(true);
    }
  }, [formActionType, telegramDefaultsApplied, editRule, formActionTelegramButtons.length]);

  const resetForm = () => {
    setFormName("");
    setFormEnabled(true);
    setFormTriggerType("object_detected");
    setFormTriggerLabel("");
    setFormTriggerPersonId("");
    setFormTriggerSensitivity("medium");
    setFormTriggerAudioLabel("baby_cry");
    setFormTriggerAudioMinScore("0.35");
    setFormTriggerLineDirection("any");
    setFormTriggerGeomCamId("");
    setFormTriggerGeomPoints([]);
    setFormTriggerLoiterSeconds("30");
    setFormTriggerObjectClass("");
    setFormCondCameras([]);
    setFormScheduleMode("always");
    setFormCondDays([]);
    setFormCondTimeAfter("");
    setFormCondTimeBefore("");
    setFormCondConfidence("any");
    setFormActionType("notify");
    setFormActionUrl("");
    setFormActionMethod("POST");
    setFormActionMessage("");
    setFormActionSeverity("info");
    setFormActionAuthType("none");
    setFormActionAuthToken("");
    setFormActionAuthHeader("X-API-Key");
    setFormActionAuthKey("");
    setFormActionAuthUser("");
    setFormActionAuthPass("");
    setFormActionPayloadTemplate("");
    setFormActionUseCustomPayload(false);
    setFormPayloadError("");
    setFormActionEmailTo("");
    setFormActionEmailSubject("");
    setFormActionEmailBody("");
    setFormActionTelegramChannelId("");
    setFormActionTelegramTemplate(DEFAULT_TELEGRAM_TEMPLATE_FALLBACK);
    setFormActionTelegramSilent(false);
    setFormActionTelegramThumbnail(false);
    setFormActionTelegramButtons(TELEGRAM_DEFAULT_BUTTONS);
    setTelegramDefaultsApplied(false);
    setFormCooldown("300");
    setFormError("");
  };

  // Hydrate from editRule when modal opens. Mirrors openEdit/openCreate
  // in the original page.tsx. Runs whenever `open` flips to true or the
  // edit target changes.
  useEffect(() => {
    if (!open) return;
    if (!editRule) {
      resetForm();
      return;
    }
    const r = editRule;
    setFormName(r.name);
    setFormEnabled(r.enabled);

    const tp = r.trigger_pattern;
    setFormTriggerType((tp.type as string) || "any");
    setFormTriggerLabel((tp.label as string) || "");
    setFormTriggerPersonId((tp.person_id as string) || "");
    setFormTriggerAudioLabel((tp.label as string) || "baby_cry");
    setFormTriggerAudioMinScore(tp.min_score != null ? String(tp.min_score) : "0.35");
    setFormTriggerLineDirection((tp.direction as string) || "any");
    setFormTriggerGeomCamId((tp.camera_id as string) || "");
    const pts = tp.points as number[][] | undefined;
    setFormTriggerGeomPoints(Array.isArray(pts) ? pts : []);
    setFormTriggerLoiterSeconds(tp.threshold_seconds != null ? String(tp.threshold_seconds) : "30");
    setFormTriggerObjectClass((tp.label as string) || "");
    setFormTriggerClapCount(tp.count != null ? String(tp.count) : "2");
    setFormTriggerPhrases(Array.isArray(tp.phrases) ? (tp.phrases as string[]) : []);
    setFormTriggerPhraseMatch((tp.match as "any" | "all") === "all" ? "all" : "any");
    const ms = tp.min_score as number | undefined;
    if (ms != null) {
      if (ms <= 0.02) setFormTriggerSensitivity("very_high");
      else if (ms <= 0.05) setFormTriggerSensitivity("high");
      else if (ms <= 0.15) setFormTriggerSensitivity("medium");
      else setFormTriggerSensitivity("low");
    } else {
      setFormTriggerSensitivity("medium");
    }

    const cond = r.conditions || {};
    const camIds = cond.camera_ids as string[] | undefined;
    const camId = cond.camera_id as string | undefined;
    setFormCondCameras(camIds || (camId ? [camId] : []));
    const days = cond.days as string[] | undefined;
    setFormCondDays(days || []);
    const hasSchedule = !!(cond.time_after || cond.time_before || (days && days.length > 0));
    setFormScheduleMode(hasSchedule ? "custom" : "always");
    setFormCondTimeAfter((cond.time_after as string) || "");
    setFormCondTimeBefore((cond.time_before as string) || "");
    const mc = cond.min_confidence as number | undefined;
    if (mc != null) {
      if (mc >= 0.8) setFormCondConfidence("very_high");
      else if (mc >= 0.6) setFormCondConfidence("high");
      else if (mc >= 0.3) setFormCondConfidence("medium");
      else setFormCondConfidence("low");
    } else {
      setFormCondConfidence("any");
    }

    const acts = Array.isArray(r.actions) ? r.actions[0] : r.actions;
    setFormActionType((acts?.type as string) || "notify");
    setFormActionUrl((acts?.url as string) || "");
    setFormActionMethod((acts?.method as string) || "POST");
    setFormActionMessage((acts?.message as string) || "");
    setFormActionSeverity((acts?.severity as string) || "info");

    const auth = acts?.auth as Record<string, string> | undefined;
    if (auth) {
      setFormActionAuthType(auth.type || "none");
      setFormActionAuthToken(auth.token || "");
      setFormActionAuthHeader(auth.header || "X-API-Key");
      setFormActionAuthKey(auth.key || "");
      setFormActionAuthUser(auth.username || "");
      setFormActionAuthPass(auth.password || "");
    } else {
      setFormActionAuthType("none");
      setFormActionAuthToken("");
      setFormActionAuthHeader("X-API-Key");
      setFormActionAuthKey("");
      setFormActionAuthUser("");
      setFormActionAuthPass("");
    }

    setFormActionEmailTo((acts?.to as string) || "");
    setFormActionEmailSubject((acts?.subject as string) || "");
    setFormActionEmailBody((acts?.body as string) || "");

    setFormActionTelegramChannelId((acts?.channel_id as string) || "");
    setFormActionTelegramTemplate(
      (acts?.template as string) || DEFAULT_TELEGRAM_TEMPLATE_FALLBACK,
    );
    setFormActionTelegramSilent(Boolean(acts?.silent));
    setFormActionTelegramThumbnail(Boolean(acts?.include_thumbnail));
    const existingButtons = Array.isArray(acts?.buttons) ? (acts?.buttons as TelegramButton[]) : [];
    setFormActionTelegramButtons(existingButtons);
    setTelegramDefaultsApplied(true);

    const pt = acts?.payload_template;
    if (pt) {
      setFormActionUseCustomPayload(true);
      setFormActionPayloadTemplate(JSON.stringify(pt, null, 2));
    } else {
      setFormActionUseCustomPayload(false);
      setFormActionPayloadTemplate("");
    }
    setFormPayloadError("");

    setFormCooldown(String(r.cooldown_seconds));
    setFormError("");
    // We intentionally depend only on open + editRule. The setters are
    // stable. The original page also reset state on each openEdit call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editRule]);

  const formSummary = useMemo(() => {
    const triggerPattern: Record<string, unknown> = { type: formTriggerType };
    if (formTriggerType === "object_detected" && formTriggerLabel) triggerPattern.label = formTriggerLabel;
    if (formTriggerType === "face_recognized" && formTriggerPersonId) triggerPattern.person_id = formTriggerPersonId;
    if (formTriggerType === "motion") triggerPattern.min_score = 0.08;
    if (formTriggerType === "audio_event") {
      triggerPattern.label = formTriggerAudioLabel;
      triggerPattern.min_score = parseFloat(formTriggerAudioMinScore) || 0.3;
    }
    if (formTriggerType === "loitering") {
      if (formTriggerGeomCamId) triggerPattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length >= 3) triggerPattern.points = formTriggerGeomPoints;
      triggerPattern.threshold_seconds = parseInt(formTriggerLoiterSeconds) || 30;
      if (formTriggerObjectClass) triggerPattern.label = formTriggerObjectClass;
    }
    if (formTriggerType === "line_cross") {
      if (formTriggerGeomCamId) triggerPattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length === 2) triggerPattern.points = formTriggerGeomPoints;
      if (formTriggerLineDirection !== "any") triggerPattern.direction = formTriggerLineDirection;
      if (formTriggerObjectClass) triggerPattern.label = formTriggerObjectClass;
    }

    const action: Record<string, unknown> = { type: formActionType };
    if (formActionType === "webhook" || formActionType === "api_call") {
      action.url = formActionUrl || "...";
      if (formActionType === "api_call") action.method = formActionMethod;
    }
    if (formActionType === "notify") {
      action.message = formActionMessage || "Rule triggered";
      action.severity = formActionSeverity;
    }

    const schedule = formScheduleMode === "custom"
      ? describeSchedule(formCondDays.length > 0 ? formCondDays : undefined, formCondTimeAfter || undefined, formCondTimeBefore || undefined)
      : "";

    return composeSummary(
      describeTrigger(triggerPattern),
      resolveCameraNames(formCondCameras, cameras),
      schedule,
      describeActions(action),
      parseInt(formCooldown) || 0,
    );
  }, [formTriggerType, formTriggerLabel, formTriggerPersonId, formTriggerAudioLabel, formTriggerAudioMinScore, formTriggerGeomCamId, formTriggerGeomPoints, formTriggerLoiterSeconds, formTriggerObjectClass, formTriggerLineDirection, formActionType, formActionUrl, formActionMethod, formActionMessage, formActionSeverity, formScheduleMode, formCondDays, formCondTimeAfter, formCondTimeBefore, formCondCameras, cameras, formCooldown]);

  const buildPayload = () => {
    const trigger_pattern: Record<string, unknown> = { type: formTriggerType };
    if (formTriggerType === "object_detected" && formTriggerLabel) {
      trigger_pattern.label = formTriggerLabel;
    }
    if (formTriggerType === "face_recognized" && formTriggerPersonId) {
      trigger_pattern.person_id = formTriggerPersonId;
    }
    if (formTriggerType === "motion") {
      const sensitivityMap: Record<string, number> = {
        very_high: 0.01,
        high: 0.03,
        medium: 0.08,
        low: 0.2,
      };
      trigger_pattern.min_score = sensitivityMap[formTriggerSensitivity] ?? 0.08;
    }
    if (formTriggerType === "audio_event") {
      trigger_pattern.label = formTriggerAudioLabel;
      trigger_pattern.min_score = parseFloat(formTriggerAudioMinScore) || 0.3;
    }
    if (formTriggerType === "clap_pattern") {
      trigger_pattern.count = parseInt(formTriggerClapCount) || 2;
    }
    if (formTriggerType === "speech_phrase") {
      trigger_pattern.phrases = formTriggerPhrases;
      trigger_pattern.match = formTriggerPhraseMatch;
    }
    if (formTriggerType === "loitering") {
      if (formTriggerGeomCamId) trigger_pattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length >= 3) trigger_pattern.points = formTriggerGeomPoints;
      trigger_pattern.threshold_seconds = parseInt(formTriggerLoiterSeconds) || 30;
      if (formTriggerObjectClass) trigger_pattern.label = formTriggerObjectClass;
    }
    if (formTriggerType === "line_cross") {
      if (formTriggerGeomCamId) trigger_pattern.camera_id = formTriggerGeomCamId;
      if (formTriggerGeomPoints.length === 2) trigger_pattern.points = formTriggerGeomPoints;
      if (formTriggerLineDirection !== "any") trigger_pattern.direction = formTriggerLineDirection;
      if (formTriggerObjectClass) trigger_pattern.label = formTriggerObjectClass;
    }

    const conditions: Record<string, unknown> = {};
    if (formCondCameras.length > 0) conditions.camera_ids = formCondCameras;
    if (formScheduleMode === "custom") {
      if (formCondTimeAfter) conditions.time_after = formCondTimeAfter;
      if (formCondTimeBefore) conditions.time_before = formCondTimeBefore;
      if (formCondDays.length > 0) conditions.days = formCondDays;
    }
    if (formCondConfidence !== "any") {
      const confMap: Record<string, number> = {
        low: 0.2,
        medium: 0.4,
        high: 0.6,
        very_high: 0.8,
      };
      conditions.min_confidence = confMap[formCondConfidence] ?? 0.4;
    }

    const action: Record<string, unknown> = { type: formActionType };
    if (formActionType === "webhook" || formActionType === "api_call") {
      action.url = formActionUrl;
      if (formActionType === "api_call") {
        action.method = formActionMethod;
      }

      if (formActionAuthType !== "none") {
        const auth: Record<string, string> = { type: formActionAuthType };
        if (formActionAuthType === "bearer") auth.token = formActionAuthToken;
        if (formActionAuthType === "api_key") {
          auth.header = formActionAuthHeader;
          auth.key = formActionAuthKey;
        }
        if (formActionAuthType === "basic") {
          auth.username = formActionAuthUser;
          auth.password = formActionAuthPass;
        }
        action.auth = auth;
      }

      if (formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
        try {
          action.payload_template = JSON.parse(formActionPayloadTemplate);
        } catch {
          // Will be caught by validation
        }
      }
    }
    if (formActionType === "broadcast" && formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
      try {
        action.payload_template = JSON.parse(formActionPayloadTemplate);
      } catch {
        // Will be caught by validation
      }
    }
    if (formActionType === "notify") {
      action.message = formActionMessage || "Rule '{rule_name}' triggered";
      action.severity = formActionSeverity;
    }
    if (formActionType === "email") {
      action.to = formActionEmailTo;
      action.subject = formActionEmailSubject || "Nurby alert. {{rule_name}}";
      action.body = formActionEmailBody || "Rule {{rule_name}} fired at {{timestamp}}";
    }
    if (formActionType === "telegram") {
      action.channel_id = formActionTelegramChannelId;
      action.template = formActionTelegramTemplate;
      action.silent = formActionTelegramSilent;
      action.include_thumbnail = formActionTelegramThumbnail;
      if (formActionTelegramButtons.length > 0) {
        action.buttons = formActionTelegramButtons.map((b) => {
          const out: Record<string, unknown> = { label: b.label, action: b.action };
          if (b.action === "open") out.url = b.url || "{event_url}";
          if (b.action === "mute_event" || b.action === "snooze_rule") {
            if (b.duration_seconds && b.duration_seconds > 0) {
              out.duration_seconds = b.duration_seconds;
            }
          }
          return out;
        });
      }
    }
    if (formActionType === "vlm_call") {
      action.provider = formVlmProvider;
      action.model = formVlmModel;
      action.system = formVlmSystem;
      action.prompt = formVlmPrompt;
      action.attach_image = formVlmAttachImage;
      action.output = formVlmOutput || "result";
      action.max_retries = parseInt(formVlmMaxRetries) || 1;
      action.on_error = formVlmOnError;
      action.timeout_ms = parseInt(formVlmTimeoutMs) || 20000;
      if (formVlmUseSchema && formVlmSchemaText.trim()) {
        try {
          action.response_schema = JSON.parse(formVlmSchemaText);
        } catch {
          // caught in validation
        }
      }
    }

    return {
      name: formName.trim(),
      enabled: formEnabled,
      trigger_pattern,
      conditions: Object.keys(conditions).length > 0 ? conditions : null,
      actions: action,
      cooldown_seconds: parseInt(formCooldown) || 300,
    };
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError("Name is required");
      return;
    }
    if ((formActionType === "webhook" || formActionType === "api_call") && !formActionUrl.trim()) {
      setFormError("URL is required");
      return;
    }
    if (formActionType === "email" && !formActionEmailTo.trim()) {
      setFormError("Recipient email is required");
      return;
    }
    if (formActionType === "telegram") {
      if (!formActionTelegramChannelId) {
        setFormError("Pick a Telegram channel");
        return;
      }
      if (!formActionTelegramTemplate.trim()) {
        setFormError("Telegram message template cannot be empty");
        return;
      }
      if (formActionTelegramButtons.length > 4) {
        setFormError("Telegram supports at most 4 inline buttons");
        return;
      }
      for (let i = 0; i < formActionTelegramButtons.length; i++) {
        const b = formActionTelegramButtons[i];
        if (!b.label.trim()) {
          setFormError(`Button ${i + 1}: label is required`);
          return;
        }
        if (b.action === "open") {
          if (!b.url || !isValidHttpUrlOrTemplate(b.url)) {
            setFormError(`Button ${i + 1}: URL must start with http(s) or use a template variable`);
            return;
          }
        }
        if (
          (b.action === "mute_event" || b.action === "snooze_rule") &&
          b.duration_seconds !== undefined &&
          (b.duration_seconds < 60 || b.duration_seconds > 24 * 3600)
        ) {
          setFormError(`Button ${i + 1}: duration must be between 60s and 24h`);
          return;
        }
      }
    }
    if (formActionUseCustomPayload && formActionPayloadTemplate.trim()) {
      try {
        JSON.parse(formActionPayloadTemplate);
      } catch {
        setFormError("Payload template is not valid JSON");
        return;
      }
    }

    setSubmitting(true);
    setFormError("");
    const body = buildPayload();

    try {
      let res: Response;
      if (editRule) {
        res = await authFetch(`/api/rules/${editRule.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        res = await authFetch("/api/rules", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }

      if (!res.ok) {
        setFormError("Failed to save rule");
        return;
      }

      onClose();
      onSaved();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-3xl shadow-xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">
          {editRule ? "Edit rule" : "Create rule"}
        </h2>

        <div className="space-y-4">
          {/* Name */}
          <div>
            <label className="text-xs font-medium text-muted-foreground block mb-1">
              Rule name
            </label>
            <input
              type="text"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm focus:outline-none focus:border-accent"
              placeholder="e.g. Person at front door"
              autoFocus
            />
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={formEnabled}
              onChange={(e) => setFormEnabled(e.target.checked)}
              className="accent-green-500"
            />
            <span className="text-sm">Enabled</span>
          </label>

          <TriggerSection
            cameras={cameras}
            persons={persons}
            activeModels={activeModels}
            modelClasses={modelClasses}
            modelClassesLoading={modelClassesLoading}
            formTriggerType={formTriggerType}
            setFormTriggerType={setFormTriggerType}
            formTriggerLabel={formTriggerLabel}
            setFormTriggerLabel={setFormTriggerLabel}
            formTriggerPersonId={formTriggerPersonId}
            setFormTriggerPersonId={setFormTriggerPersonId}
            formTriggerSensitivity={formTriggerSensitivity}
            setFormTriggerSensitivity={setFormTriggerSensitivity}
            formTriggerAudioLabel={formTriggerAudioLabel}
            setFormTriggerAudioLabel={setFormTriggerAudioLabel}
            formTriggerAudioMinScore={formTriggerAudioMinScore}
            setFormTriggerAudioMinScore={setFormTriggerAudioMinScore}
            formTriggerLineDirection={formTriggerLineDirection}
            setFormTriggerLineDirection={setFormTriggerLineDirection}
            formTriggerGeomCamId={formTriggerGeomCamId}
            setFormTriggerGeomCamId={setFormTriggerGeomCamId}
            formTriggerGeomPoints={formTriggerGeomPoints}
            setFormTriggerGeomPoints={setFormTriggerGeomPoints}
            formTriggerLoiterSeconds={formTriggerLoiterSeconds}
            setFormTriggerLoiterSeconds={setFormTriggerLoiterSeconds}
            formTriggerObjectClass={formTriggerObjectClass}
            setFormTriggerObjectClass={setFormTriggerObjectClass}
            formTriggerClapCount={formTriggerClapCount}
            setFormTriggerClapCount={setFormTriggerClapCount}
            formTriggerPhrases={formTriggerPhrases}
            setFormTriggerPhrases={setFormTriggerPhrases}
            formTriggerPhraseMatch={formTriggerPhraseMatch}
            setFormTriggerPhraseMatch={setFormTriggerPhraseMatch}
          />

          <ConditionsSection
            cameras={cameras}
            formCondCameras={formCondCameras}
            setFormCondCameras={setFormCondCameras}
            formScheduleMode={formScheduleMode}
            setFormScheduleMode={setFormScheduleMode}
            formCondDays={formCondDays}
            setFormCondDays={setFormCondDays}
            formCondTimeAfter={formCondTimeAfter}
            setFormCondTimeAfter={setFormCondTimeAfter}
            formCondTimeBefore={formCondTimeBefore}
            setFormCondTimeBefore={setFormCondTimeBefore}
            formCondConfidence={formCondConfidence}
            setFormCondConfidence={setFormCondConfidence}
          />

          <ActionsSection
            telegramChannels={telegramChannels}
            telegramChannelsLoading={telegramChannelsLoading}
            formActionType={formActionType}
            setFormActionType={setFormActionType}
            formActionUrl={formActionUrl}
            setFormActionUrl={setFormActionUrl}
            formActionMethod={formActionMethod}
            setFormActionMethod={setFormActionMethod}
            formActionMessage={formActionMessage}
            setFormActionMessage={setFormActionMessage}
            formActionSeverity={formActionSeverity}
            setFormActionSeverity={setFormActionSeverity}
            formActionAuthType={formActionAuthType}
            setFormActionAuthType={setFormActionAuthType}
            formActionAuthToken={formActionAuthToken}
            setFormActionAuthToken={setFormActionAuthToken}
            formActionAuthHeader={formActionAuthHeader}
            setFormActionAuthHeader={setFormActionAuthHeader}
            formActionAuthKey={formActionAuthKey}
            setFormActionAuthKey={setFormActionAuthKey}
            formActionAuthUser={formActionAuthUser}
            setFormActionAuthUser={setFormActionAuthUser}
            formActionAuthPass={formActionAuthPass}
            setFormActionAuthPass={setFormActionAuthPass}
            formActionPayloadTemplate={formActionPayloadTemplate}
            setFormActionPayloadTemplate={setFormActionPayloadTemplate}
            formActionUseCustomPayload={formActionUseCustomPayload}
            setFormActionUseCustomPayload={setFormActionUseCustomPayload}
            formPayloadError={formPayloadError}
            setFormPayloadError={setFormPayloadError}
            formActionEmailTo={formActionEmailTo}
            setFormActionEmailTo={setFormActionEmailTo}
            formActionEmailSubject={formActionEmailSubject}
            setFormActionEmailSubject={setFormActionEmailSubject}
            formActionEmailBody={formActionEmailBody}
            setFormActionEmailBody={setFormActionEmailBody}
            formActionTelegramChannelId={formActionTelegramChannelId}
            setFormActionTelegramChannelId={setFormActionTelegramChannelId}
            formActionTelegramTemplate={formActionTelegramTemplate}
            setFormActionTelegramTemplate={setFormActionTelegramTemplate}
            formActionTelegramSilent={formActionTelegramSilent}
            setFormActionTelegramSilent={setFormActionTelegramSilent}
            formActionTelegramThumbnail={formActionTelegramThumbnail}
            setFormActionTelegramThumbnail={setFormActionTelegramThumbnail}
            formActionTelegramButtons={formActionTelegramButtons}
            setFormActionTelegramButtons={setFormActionTelegramButtons}
            formVlmProvider={formVlmProvider}
            setFormVlmProvider={setFormVlmProvider}
            formVlmModel={formVlmModel}
            setFormVlmModel={setFormVlmModel}
            formVlmSystem={formVlmSystem}
            setFormVlmSystem={setFormVlmSystem}
            formVlmPrompt={formVlmPrompt}
            setFormVlmPrompt={setFormVlmPrompt}
            formVlmAttachImage={formVlmAttachImage}
            setFormVlmAttachImage={setFormVlmAttachImage}
            formVlmUseSchema={formVlmUseSchema}
            setFormVlmUseSchema={setFormVlmUseSchema}
            formVlmSchemaText={formVlmSchemaText}
            setFormVlmSchemaText={setFormVlmSchemaText}
            formVlmOutput={formVlmOutput}
            setFormVlmOutput={setFormVlmOutput}
            formVlmMaxRetries={formVlmMaxRetries}
            setFormVlmMaxRetries={setFormVlmMaxRetries}
            formVlmOnError={formVlmOnError}
            setFormVlmOnError={setFormVlmOnError}
            formVlmTimeoutMs={formVlmTimeoutMs}
            setFormVlmTimeoutMs={setFormVlmTimeoutMs}
          />

          {/* Cooldown */}
          <div>
            <label className="text-xs font-medium text-muted-foreground block mb-1">
              Wait between alerts
            </label>
            <div className="grid grid-cols-5 gap-1">
              {[
                { value: "0", label: "None" },
                { value: "30", label: "30 sec" },
                { value: "300", label: "5 min" },
                { value: "900", label: "15 min" },
                { value: "3600", label: "1 hour" },
              ].map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setFormCooldown(opt.value)}
                  className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                    formCooldown === opt.value
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <span className="text-[10px] text-muted-foreground">
              Prevents repeated alerts for the same event
            </span>
          </div>

          <SummaryCard text={formSummary} className="p-3" />

          {formError && (
            <div className="text-xs text-red-400">{formError}</div>
          )}
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="px-3 py-1.5 text-sm rounded-md bg-foreground text-background font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Saving." : editRule ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
