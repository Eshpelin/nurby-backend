"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";

interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  trigger_pattern: Record<string, unknown>;
  conditions: Record<string, unknown> | null;
  actions: Record<string, unknown> | Record<string, unknown>[];
  cooldown_seconds: number;
  created_at: string;
}

interface EventEntry {
  id: string;
  rule_id: string | null;
  observation_id: string | null;
  fired_at: string;
  payload: Record<string, unknown> | null;
  acknowledged_at: string | null;
  action_status: string;
  action_error: string | null;
  action_type: string | null;
}

interface Camera {
  id: string;
  name: string;
  status: string;
}

const TRIGGER_TYPES = [
  { value: "object_detected", label: "Object detected" },
  { value: "face_detected", label: "Face detected" },
  { value: "face_recognized", label: "Face recognized" },
  { value: "face_unknown", label: "Unknown face" },
  { value: "motion", label: "Motion" },
  { value: "any", label: "Any observation" },
];

const OBJECT_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle",
  "dog", "cat", "bird", "backpack", "handbag",
  "suitcase", "umbrella",
];

const ACTION_TYPES = [
  { value: "webhook", label: "Webhook" },
  { value: "api_call", label: "API Call" },
  { value: "broadcast", label: "WebSocket broadcast" },
  { value: "notify", label: "Notification" },
  { value: "email", label: "Email" },
];

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

const AUTH_TYPES = [
  { value: "none", label: "No auth" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key header" },
  { value: "basic", label: "Basic auth" },
];

const TEMPLATE_VARIABLES = [
  { key: "event_id", desc: "Event UUID" },
  { key: "rule_name", desc: "Rule name" },
  { key: "camera_id", desc: "Camera UUID" },
  { key: "timestamp", desc: "ISO timestamp" },
  { key: "motion_score", desc: "Motion score (0-1)" },
  { key: "object_detections", desc: "Detection results (object)" },
  { key: "person_detections", desc: "Face results (object)" },
  { key: "vlm_description", desc: "VLM scene description" },
  { key: "confidence", desc: "VLM confidence" },
  { key: "observation_id", desc: "Observation UUID" },
];

const DEFAULT_PAYLOAD_TEMPLATE = `{
  "event": "{{rule_name}}",
  "camera": "{{camera_id}}",
  "timestamp": "{{timestamp}}",
  "description": "{{vlm_description}}",
  "detections": "{{object_detections}}"
}`;

function describeTrigger(pattern: Record<string, unknown>): string {
  const t = pattern.type as string;
  if (t === "object_detected") {
    const label = pattern.label as string | undefined;
    return label ? `When "${label}" detected` : "When any object detected";
  }
  if (t === "face_detected") return "When any face detected";
  if (t === "face_recognized") {
    const pid = pattern.person_id as string | undefined;
    return pid ? `When person ${pid.slice(0, 8)} recognized` : "When any known face recognized";
  }
  if (t === "face_unknown") return "When unknown face detected";
  if (t === "motion") {
    const ms = pattern.min_score as number | undefined;
    return ms ? `When motion score >= ${ms}` : "When motion detected";
  }
  if (t === "any") return "On every observation";
  return "Unknown trigger";
}

function describeActions(actions: Record<string, unknown> | Record<string, unknown>[]): string {
  const list = Array.isArray(actions) ? actions : [actions];
  return list
    .map((a) => {
      if (a.type === "webhook") {
        const hasAuth = !!(a.auth as Record<string, unknown> | undefined);
        return `POST to ${(a.url as string) || "..."}${hasAuth ? " (authenticated)" : ""}`;
      }
      if (a.type === "api_call") {
        const method = (a.method as string) || "POST";
        const hasAuth = !!(a.auth as Record<string, unknown> | undefined);
        return `${method} ${(a.url as string) || "..."}${hasAuth ? " (authenticated)" : ""}`;
      }
      if (a.type === "broadcast") return "Broadcast via WebSocket";
      if (a.type === "notify") return `Notify. "${(a.message as string) || "..."}"`;
      if (a.type === "email") return `Email to ${(a.to as string) || "..."}`;
      return String(a.type);
    })
    .join(", ");
}

function formatCooldown(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${seconds} seconds`;
  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "1 minute";
  return `${minutes} minutes`;
}

function resolveCameraNames(camIds: string[], cameras: Camera[]): string {
  if (camIds.length === 0) return "any camera";
  const names = camIds.map((cid) => {
    const cam = cameras.find((c) => c.id === cid);
    return cam ? cam.name : cid.slice(0, 8);
  });
  return names.join(", ");
}

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"];
const WEEKEND = ["sat", "sun"];

function describeSchedule(days: string[] | undefined, timeAfter: string | undefined, timeBefore: string | undefined): string {
  const scheduleParts: string[] = [];
  if (days && days.length > 0 && days.length < 7) {
    const isWeekdays = WEEKDAYS.every((d) => days.includes(d)) && days.length === 5;
    const isWeekend = WEEKEND.every((d) => days.includes(d)) && days.length === 2;
    if (isWeekdays) scheduleParts.push("on weekdays");
    else if (isWeekend) scheduleParts.push("on weekends");
    else scheduleParts.push(`on ${days.map((d) => d.charAt(0).toUpperCase() + d.slice(1)).join(", ")}`);
  }
  if (timeAfter || timeBefore) {
    scheduleParts.push(`between ${timeAfter || "00:00"} and ${timeBefore || "23:59"}`);
  }
  return scheduleParts.join(" ");
}

function composeSummary(
  trigger: string,
  cameraLabel: string,
  schedule: string,
  actionLabel: string,
  cooldownSeconds: number,
): string {
  const parts = [trigger, `on ${cameraLabel}`];
  if (schedule) parts.push(schedule);
  parts.push(actionLabel);
  let sentence = parts.join(", ") + ".";
  if (cooldownSeconds > 0) {
    sentence += ` Cooldown. ${formatCooldown(cooldownSeconds)}.`;
  }
  return sentence;
}

function buildRuleSummary(rule: Rule, cameras: Camera[]): string {
  const cond = rule.conditions || {};
  const camIds = (cond.camera_ids as string[]) || (cond.camera_id ? [cond.camera_id as string] : []);
  return composeSummary(
    describeTrigger(rule.trigger_pattern),
    resolveCameraNames(camIds, cameras),
    describeSchedule(cond.days as string[] | undefined, cond.time_after as string | undefined, cond.time_before as string | undefined),
    describeActions(rule.actions),
    rule.cooldown_seconds,
  );
}

function SummaryCard({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`bg-blue-500/10 border border-blue-500/20 rounded-lg text-sm text-zinc-200 flex gap-3 items-start ${className || "p-4"}`}>
      <span className="text-base leading-none mt-0.5">💡</span>
      <span>{text}</span>
    </div>
  );
}

export default function RulesPage() {
  const { authFetch } = useAuth();
  const [rules, setRules] = useState<Rule[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editRule, setEditRule] = useState<Rule | null>(null);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);
  const [ruleEvents, setRuleEvents] = useState<EventEntry[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  // Form state
  const [formName, setFormName] = useState("");
  const [formEnabled, setFormEnabled] = useState(true);
  const [formTriggerType, setFormTriggerType] = useState("object_detected");
  const [formTriggerLabel, setFormTriggerLabel] = useState("");
  const [formTriggerPersonId, setFormTriggerPersonId] = useState("");
  const [formTriggerSensitivity, setFormTriggerSensitivity] = useState("medium");
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
  const [formCooldown, setFormCooldown] = useState("300");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const fetchRules = useCallback(async () => {
    try {
      const res = await authFetch("/api/rules");
      if (res.ok) setRules(await res.json());
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCameras = useCallback(async () => {
    try {
      const res = await authFetch("/api/cameras");
      if (res.ok) setCameras(await res.json());
    } catch {
      /* silent */
    }
  }, []);

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
  }, []);

  useEffect(() => {
    fetchRules();
    fetchCameras();
  }, [fetchRules, fetchCameras]);

  // Fetch events when a rule is selected, auto-refresh every 30s
  useEffect(() => {
    if (!selectedRule) {
      setRuleEvents([]);
      return;
    }
    fetchRuleEvents(selectedRule.id);
    const interval = setInterval(() => fetchRuleEvents(selectedRule.id), 30000);
    return () => clearInterval(interval);
  }, [selectedRule, fetchRuleEvents]);

  const resetForm = () => {
    setFormName("");
    setFormEnabled(true);
    setFormTriggerType("object_detected");
    setFormTriggerLabel("");
    setFormTriggerPersonId("");
    setFormTriggerSensitivity("medium");
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
    setFormCooldown("300");
    setFormError("");
  };

  const openCreate = () => {
    setEditRule(null);
    resetForm();
    setShowModal(true);
  };

  const openEdit = (r: Rule) => {
    setEditRule(r);
    setFormName(r.name);
    setFormEnabled(r.enabled);

    const tp = r.trigger_pattern;
    setFormTriggerType((tp.type as string) || "any");
    setFormTriggerLabel((tp.label as string) || "");
    setFormTriggerPersonId((tp.person_id as string) || "");
    // Map min_score back to sensitivity level
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
    // Map min_confidence back to label
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

    // Restore auth config
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

    // Restore email fields
    setFormActionEmailTo((acts?.to as string) || "");
    setFormActionEmailSubject((acts?.subject as string) || "");
    setFormActionEmailBody((acts?.body as string) || "");

    // Restore payload template
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
    setShowModal(true);
  };

  const formSummary = useMemo(() => {
    const triggerPattern: Record<string, unknown> = { type: formTriggerType };
    if (formTriggerType === "object_detected" && formTriggerLabel) triggerPattern.label = formTriggerLabel;
    if (formTriggerType === "face_recognized" && formTriggerPersonId) triggerPattern.person_id = formTriggerPersonId;
    if (formTriggerType === "motion") triggerPattern.min_score = 0.08;

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
  }, [formTriggerType, formTriggerLabel, formTriggerPersonId, formActionType, formActionUrl, formActionMethod, formActionMessage, formActionSeverity, formScheduleMode, formCondDays, formCondTimeAfter, formCondTimeBefore, formCondCameras, cameras, formCooldown]);

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

      // Auth config
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

      // Custom payload template
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

      setShowModal(false);
      fetchRules();
    } catch {
      setFormError("Network error");
    } finally {
      setSubmitting(false);
    }
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
          {/* Rule list */}
          <section className="col-span-8 space-y-3">
            {rules.map((r) => (
              <div
                key={r.id}
                onClick={() => setSelectedRule(r)}
                className={`rounded-lg border p-4 cursor-pointer transition-colors ${
                  selectedRule?.id === r.id
                    ? "border-accent bg-card"
                    : "border-border bg-card hover:border-muted-foreground/30"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggle(r);
                      }}
                      className={`w-8 h-5 rounded-full relative transition-colors ${
                        r.enabled ? "bg-green-500" : "bg-muted"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                          r.enabled ? "left-3.5" : "left-0.5"
                        }`}
                      />
                    </button>
                    <div>
                      <div className="font-medium">{r.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {describeTrigger(r.trigger_pattern)}
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openEdit(r);
                      }}
                      className="px-2 py-1 text-xs rounded border border-border hover:bg-muted transition-colors"
                    >
                      Edit
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(r.id);
                      }}
                      className="px-2 py-1 text-xs rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors"
                    >
                      Del
                    </button>
                  </div>
                </div>
                <div className="mt-2 text-xs italic text-muted-foreground/80 leading-relaxed">
                  {buildRuleSummary(r, cameras)}
                </div>
              </div>
            ))}
          </section>

          {/* Preview panel */}
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

            {/* Execution Log */}
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
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      )}

      {/* Create / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowModal(false)}
          />
          <div className="relative bg-card border border-border rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
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

              {/* Enabled */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formEnabled}
                  onChange={(e) => setFormEnabled(e.target.checked)}
                  className="accent-green-500"
                />
                <span className="text-sm">Enabled</span>
              </label>

              {/* Trigger */}
              <fieldset className="border border-border rounded-md p-3 space-y-2">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  Trigger
                </legend>
                <select
                  value={formTriggerType}
                  onChange={(e) => setFormTriggerType(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                >
                  {TRIGGER_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>

                {formTriggerType === "object_detected" && (
                  <select
                    value={formTriggerLabel}
                    onChange={(e) => setFormTriggerLabel(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                  >
                    <option value="">Any object</option>
                    {OBJECT_LABELS.map((l) => (
                      <option key={l} value={l}>
                        {l}
                      </option>
                    ))}
                  </select>
                )}

                {formTriggerType === "face_recognized" && (
                  <input
                    type="text"
                    value={formTriggerPersonId}
                    onChange={(e) => setFormTriggerPersonId(e.target.value)}
                    className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                    placeholder="Person ID (leave blank for any recognized face)"
                  />
                )}

                {formTriggerType === "motion" && (
                  <div>
                    <label className="text-xs text-muted-foreground block mb-1.5">
                      Motion sensitivity
                    </label>
                    <div className="grid grid-cols-4 gap-1">
                      {[
                        { value: "very_high", label: "Any movement", desc: "Triggers on smallest change" },
                        { value: "high", label: "Sensitive", desc: "Small movements" },
                        { value: "medium", label: "Normal", desc: "Moderate activity" },
                        { value: "low", label: "Only major", desc: "Large movements only" },
                      ].map((s) => (
                        <button
                          key={s.value}
                          type="button"
                          onClick={() => setFormTriggerSensitivity(s.value)}
                          className={`px-2 py-2 text-xs rounded border transition-colors text-center ${
                            formTriggerSensitivity === s.value
                              ? "border-accent bg-accent/10 text-accent"
                              : "border-border hover:bg-muted"
                          }`}
                        >
                          <div className="font-medium">{s.label}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </fieldset>

              {/* Conditions */}
              <fieldset className="border border-border rounded-md p-3 space-y-2">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  Conditions (optional)
                </legend>
                <div>
                  <label className="text-xs text-muted-foreground block mb-1">Cameras</label>
                  {cameras.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No cameras added yet</p>
                  ) : (
                    <div className="space-y-1.5 max-h-36 overflow-y-auto rounded-md border border-border bg-background p-2">
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="checkbox"
                          checked={formCondCameras.length === 0}
                          onChange={() => setFormCondCameras([])}
                          className="accent-green-500"
                        />
                        <span className="text-muted-foreground">All cameras</span>
                      </label>
                      {cameras.map((cam) => (
                        <label key={cam.id} className="flex items-center gap-2 cursor-pointer text-sm">
                          <input
                            type="checkbox"
                            checked={formCondCameras.includes(cam.id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setFormCondCameras([...formCondCameras, cam.id]);
                              } else {
                                setFormCondCameras(formCondCameras.filter((c) => c !== cam.id));
                              }
                            }}
                            className="accent-green-500"
                          />
                          <span>{cam.name}</span>
                          <span className={`w-1.5 h-1.5 rounded-full ${
                            cam.status === "recording" ? "bg-green-500" : cam.status === "online" ? "bg-accent" : "bg-muted-foreground/40"
                          }`} />
                        </label>
                      ))}
                    </div>
                  )}
                </div>
                {/* Schedule */}
                <div>
                  <label className="text-xs text-muted-foreground block mb-1.5">Schedule</label>
                  <div className="flex gap-1 mb-2">
                    <button
                      type="button"
                      onClick={() => setFormScheduleMode("always")}
                      className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                        formScheduleMode === "always"
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      Always on
                    </button>
                    <button
                      type="button"
                      onClick={() => setFormScheduleMode("custom")}
                      className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                        formScheduleMode === "custom"
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border hover:bg-muted"
                      }`}
                    >
                      Custom schedule
                    </button>
                  </div>

                  {formScheduleMode === "custom" && (
                    <div className="space-y-2 pl-1">
                      {/* Days of week */}
                      <div>
                        <label className="text-[10px] text-muted-foreground block mb-1">Active on</label>
                        <div className="flex gap-1">
                          {[
                            { value: "mon", label: "M" },
                            { value: "tue", label: "T" },
                            { value: "wed", label: "W" },
                            { value: "thu", label: "T" },
                            { value: "fri", label: "F" },
                            { value: "sat", label: "S" },
                            { value: "sun", label: "S" },
                          ].map((day) => (
                            <button
                              key={day.value}
                              type="button"
                              onClick={() => {
                                setFormCondDays((prev) =>
                                  prev.includes(day.value)
                                    ? prev.filter((d) => d !== day.value)
                                    : [...prev, day.value]
                                );
                              }}
                              className={`w-8 h-8 text-xs rounded-full border transition-colors ${
                                formCondDays.includes(day.value)
                                  ? "border-accent bg-accent/20 text-accent"
                                  : "border-border hover:bg-muted text-muted-foreground"
                              }`}
                            >
                              {day.label}
                            </button>
                          ))}
                          <button
                            type="button"
                            onClick={() => {
                              if (formCondDays.length === 7) {
                                setFormCondDays([]);
                              } else {
                                setFormCondDays(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
                              }
                            }}
                            className="px-2 h-8 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground ml-1"
                          >
                            {formCondDays.length === 7 ? "None" : "All"}
                          </button>
                        </div>
                        {formCondDays.length === 0 && (
                          <span className="text-[10px] text-muted-foreground">No days selected = every day</span>
                        )}
                      </div>

                      {/* Time range */}
                      <div>
                        <label className="text-[10px] text-muted-foreground block mb-1">Active between</label>
                        <div className="flex items-center gap-2">
                          <input
                            type="time"
                            value={formCondTimeAfter}
                            onChange={(e) => setFormCondTimeAfter(e.target.value)}
                            className="flex-1 px-2 py-1.5 rounded-md bg-background border border-border text-sm"
                          />
                          <span className="text-xs text-muted-foreground">to</span>
                          <input
                            type="time"
                            value={formCondTimeBefore}
                            onChange={(e) => setFormCondTimeBefore(e.target.value)}
                            className="flex-1 px-2 py-1.5 rounded-md bg-background border border-border text-sm"
                          />
                        </div>
                        {!formCondTimeAfter && !formCondTimeBefore && (
                          <span className="text-[10px] text-muted-foreground">No times set = all day</span>
                        )}
                      </div>

                      {/* Quick presets */}
                      <div className="flex gap-1">
                        {[
                          { label: "Daytime", after: "07:00", before: "19:00", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] },
                          { label: "Nighttime", after: "19:00", before: "07:00", days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] },
                          { label: "Weekdays", after: "", before: "", days: ["mon", "tue", "wed", "thu", "fri"] },
                          { label: "Weekends", after: "", before: "", days: ["sat", "sun"] },
                        ].map((preset) => (
                          <button
                            key={preset.label}
                            type="button"
                            onClick={() => {
                              setFormCondTimeAfter(preset.after);
                              setFormCondTimeBefore(preset.before);
                              setFormCondDays(preset.days);
                            }}
                            className="px-2 py-1 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground transition-colors"
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Detection confidence */}
                <div>
                  <label className="text-xs text-muted-foreground block mb-1.5">
                    Detection confidence
                  </label>
                  <div className="grid grid-cols-5 gap-1">
                    {[
                      { value: "any", label: "Any", desc: "All detections" },
                      { value: "low", label: "Low+", desc: "20%+" },
                      { value: "medium", label: "Medium+", desc: "40%+" },
                      { value: "high", label: "High+", desc: "60%+" },
                      { value: "very_high", label: "Very high", desc: "80%+" },
                    ].map((c) => (
                      <button
                        key={c.value}
                        type="button"
                        onClick={() => setFormCondConfidence(c.value)}
                        className={`px-1 py-1.5 text-[11px] rounded border transition-colors text-center ${
                          formCondConfidence === c.value
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border hover:bg-muted"
                        }`}
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                  <span className="text-[10px] text-muted-foreground">
                    Higher confidence = fewer false positives but may miss some detections
                  </span>
                </div>
              </fieldset>

              {/* Action */}
              <fieldset className="border border-border rounded-md p-3 space-y-3">
                <legend className="text-xs font-medium text-muted-foreground px-1">
                  Action
                </legend>
                <select
                  value={formActionType}
                  onChange={(e) => setFormActionType(e.target.value)}
                  className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                >
                  {ACTION_TYPES.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>

                {/* Webhook / API Call fields */}
                {(formActionType === "webhook" || formActionType === "api_call") && (
                  <div className="space-y-3">
                    {/* Method selector for API call */}
                    {formActionType === "api_call" && (
                      <div>
                        <label className="text-xs text-muted-foreground block mb-1">HTTP Method</label>
                        <div className="flex gap-1">
                          {HTTP_METHODS.map((m) => (
                            <button
                              key={m}
                              type="button"
                              onClick={() => setFormActionMethod(m)}
                              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                                formActionMethod === m
                                  ? "border-accent bg-accent/10 text-accent"
                                  : "border-border hover:bg-muted"
                              }`}
                            >
                              {m}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* URL */}
                    <input
                      type="url"
                      value={formActionUrl}
                      onChange={(e) => setFormActionUrl(e.target.value)}
                      className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                      placeholder="https://api.example.com/endpoint"
                    />

                    {/* Authentication */}
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1.5">Authentication</label>
                      <div className="flex gap-1 mb-2">
                        {AUTH_TYPES.map((at) => (
                          <button
                            key={at.value}
                            type="button"
                            onClick={() => setFormActionAuthType(at.value)}
                            className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                              formActionAuthType === at.value
                                ? "border-accent bg-accent/10 text-accent"
                                : "border-border hover:bg-muted"
                            }`}
                          >
                            {at.label}
                          </button>
                        ))}
                      </div>

                      {formActionAuthType === "bearer" && (
                        <input
                          type="password"
                          value={formActionAuthToken}
                          onChange={(e) => setFormActionAuthToken(e.target.value)}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                          placeholder="Bearer token"
                        />
                      )}

                      {formActionAuthType === "api_key" && (
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={formActionAuthHeader}
                            onChange={(e) => setFormActionAuthHeader(e.target.value)}
                            className="w-1/3 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Header name"
                          />
                          <input
                            type="password"
                            value={formActionAuthKey}
                            onChange={(e) => setFormActionAuthKey(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="API key value"
                          />
                        </div>
                      )}

                      {formActionAuthType === "basic" && (
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={formActionAuthUser}
                            onChange={(e) => setFormActionAuthUser(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Username"
                          />
                          <input
                            type="password"
                            value={formActionAuthPass}
                            onChange={(e) => setFormActionAuthPass(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md bg-background border border-border text-sm"
                            placeholder="Password"
                          />
                        </div>
                      )}
                    </div>

                    {/* Custom payload toggle + editor */}
                    <div>
                      <label className="flex items-center gap-2 cursor-pointer mb-2">
                        <input
                          type="checkbox"
                          checked={formActionUseCustomPayload}
                          onChange={(e) => {
                            setFormActionUseCustomPayload(e.target.checked);
                            if (e.target.checked && !formActionPayloadTemplate) {
                              setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                            }
                            setFormPayloadError("");
                          }}
                          className="accent-green-500"
                        />
                        <span className="text-xs">Custom payload template</span>
                      </label>

                      {formActionUseCustomPayload && (
                        <div className="space-y-2">
                          <textarea
                            value={formActionPayloadTemplate}
                            onChange={(e) => {
                              setFormActionPayloadTemplate(e.target.value);
                              setFormPayloadError("");
                              try {
                                if (e.target.value.trim()) JSON.parse(e.target.value);
                              } catch {
                                setFormPayloadError("Invalid JSON");
                              }
                            }}
                            rows={8}
                            className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                            placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                            spellCheck={false}
                          />
                          {formPayloadError && (
                            <div className="text-[10px] text-red-400">{formPayloadError}</div>
                          )}
                          <div>
                            <div className="text-[10px] text-muted-foreground mb-1">
                              Available variables (click to insert)
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {TEMPLATE_VARIABLES.map((v) => (
                                <button
                                  key={v.key}
                                  type="button"
                                  title={v.desc}
                                  onClick={() => {
                                    setFormActionPayloadTemplate(
                                      (prev) => prev + `"{{${v.key}}}"`
                                    );
                                  }}
                                  className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                                >
                                  {`{{${v.key}}}`}
                                </button>
                              ))}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Broadcast custom payload */}
                {formActionType === "broadcast" && (
                  <div>
                    <label className="flex items-center gap-2 cursor-pointer mb-2">
                      <input
                        type="checkbox"
                        checked={formActionUseCustomPayload}
                        onChange={(e) => {
                          setFormActionUseCustomPayload(e.target.checked);
                          if (e.target.checked && !formActionPayloadTemplate) {
                            setFormActionPayloadTemplate(DEFAULT_PAYLOAD_TEMPLATE);
                          }
                          setFormPayloadError("");
                        }}
                        className="accent-green-500"
                      />
                      <span className="text-xs">Custom broadcast payload</span>
                    </label>

                    {formActionUseCustomPayload && (
                      <div className="space-y-2">
                        <textarea
                          value={formActionPayloadTemplate}
                          onChange={(e) => {
                            setFormActionPayloadTemplate(e.target.value);
                            setFormPayloadError("");
                            try {
                              if (e.target.value.trim()) JSON.parse(e.target.value);
                            } catch {
                              setFormPayloadError("Invalid JSON");
                            }
                          }}
                          rows={6}
                          className="w-full px-3 py-2 rounded-md bg-background border border-border text-xs font-mono focus:outline-none focus:border-accent resize-y"
                          placeholder={DEFAULT_PAYLOAD_TEMPLATE}
                          spellCheck={false}
                        />
                        {formPayloadError && (
                          <div className="text-[10px] text-red-400">{formPayloadError}</div>
                        )}
                        <div className="flex flex-wrap gap-1">
                          {TEMPLATE_VARIABLES.map((v) => (
                            <button
                              key={v.key}
                              type="button"
                              title={v.desc}
                              onClick={() => {
                                setFormActionPayloadTemplate(
                                  (prev) => prev + `"{{${v.key}}}"`
                                );
                              }}
                              className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                            >
                              {`{{${v.key}}}`}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Notify fields */}
                {formActionType === "notify" && (
                  <>
                    <input
                      type="text"
                      value={formActionMessage}
                      onChange={(e) => setFormActionMessage(e.target.value)}
                      className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                      placeholder="Rule '{rule_name}' triggered"
                    />
                    <select
                      value={formActionSeverity}
                      onChange={(e) => setFormActionSeverity(e.target.value)}
                      className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                    >
                      <option value="info">Info</option>
                      <option value="warning">Warning</option>
                      <option value="critical">Critical</option>
                    </select>
                  </>
                )}

                {/* Email fields */}
                {formActionType === "email" && (
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Recipient</label>
                      <input
                        type="email"
                        value={formActionEmailTo}
                        onChange={(e) => setFormActionEmailTo(e.target.value)}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        placeholder="user@example.com"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Subject template</label>
                      <input
                        type="text"
                        value={formActionEmailSubject}
                        onChange={(e) => setFormActionEmailSubject(e.target.value)}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
                        placeholder="Nurby alert. {{rule_name}}"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground block mb-1">Body template</label>
                      <textarea
                        value={formActionEmailBody}
                        onChange={(e) => setFormActionEmailBody(e.target.value)}
                        rows={4}
                        className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm resize-y"
                        placeholder="Rule {{rule_name}} fired at {{timestamp}} on camera {{camera_id}}"
                      />
                    </div>
                    <div>
                      <div className="text-[10px] text-muted-foreground mb-1">
                        Available variables (click to insert into body)
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {TEMPLATE_VARIABLES.map((v) => (
                          <button
                            key={v.key}
                            type="button"
                            title={v.desc}
                            onClick={() => {
                              setFormActionEmailBody(
                                (prev) => prev + `{{${v.key}}}`
                              );
                            }}
                            className="px-1.5 py-0.5 text-[10px] rounded border border-border hover:bg-muted text-muted-foreground font-mono transition-colors"
                          >
                            {`{{${v.key}}}`}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                      SMTP must be configured in Settings for email delivery to work.
                    </div>
                  </div>
                )}
              </fieldset>

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
                onClick={() => setShowModal(false)}
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
      )}
    </div>
  );
}
