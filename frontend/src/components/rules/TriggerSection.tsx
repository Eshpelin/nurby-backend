"use client";

import {
  TRIGGER_TYPES,
  TRIGGER_ACCENTS,
  AUDIO_LABELS,
  type Camera,
  type Person,
} from "./types";
import { ModelClassPicker } from "./ModelClassPicker";
import { StyledSelect } from "./StyledSelect";
import { GeometryEditor } from "./GeometryEditor";
import { RulePhraseInput } from "./RulePhraseInput";

export interface TriggerSectionProps {
  cameras: Camera[];
  persons: Person[];
  activeModels: string[];
  modelClasses: string[];
  modelClassesLoading: boolean;

  formTriggerType: string;
  setFormTriggerType: (v: string) => void;
  formTriggerLabel: string;
  setFormTriggerLabel: (v: string) => void;
  formTriggerPersonId: string;
  setFormTriggerPersonId: (v: string) => void;
  formTriggerSensitivity: string;
  setFormTriggerSensitivity: (v: string) => void;
  formTriggerAudioLabel: string;
  setFormTriggerAudioLabel: (v: string) => void;
  formTriggerAudioMinScore: string;
  setFormTriggerAudioMinScore: (v: string) => void;
  formTriggerLineDirection: string;
  setFormTriggerLineDirection: (v: string) => void;
  formTriggerGeomCamId: string;
  setFormTriggerGeomCamId: (v: string) => void;
  formTriggerGeomPoints: number[][];
  setFormTriggerGeomPoints: (v: number[][]) => void;
  formTriggerLoiterSeconds: string;
  setFormTriggerLoiterSeconds: (v: string) => void;
  formTriggerObjectClass: string;
  setFormTriggerObjectClass: (v: string) => void;
  formTriggerClapCount: string;
  setFormTriggerClapCount: (v: string) => void;
  formTriggerPhrases: string[];
  setFormTriggerPhrases: (v: string[]) => void;
  formTriggerPhraseMatch: "any" | "all";
  setFormTriggerPhraseMatch: (v: "any" | "all") => void;
}

export function TriggerSection(props: TriggerSectionProps) {
  const {
    cameras,
    persons,
    activeModels,
    modelClasses,
    modelClassesLoading,
    formTriggerType,
    setFormTriggerType,
    formTriggerLabel,
    setFormTriggerLabel,
    formTriggerPersonId,
    setFormTriggerPersonId,
    formTriggerSensitivity,
    setFormTriggerSensitivity,
    formTriggerAudioLabel,
    setFormTriggerAudioLabel,
    formTriggerAudioMinScore,
    setFormTriggerAudioMinScore,
    formTriggerLineDirection,
    setFormTriggerLineDirection,
    formTriggerGeomCamId,
    setFormTriggerGeomCamId,
    formTriggerGeomPoints,
    setFormTriggerGeomPoints,
    formTriggerLoiterSeconds,
    setFormTriggerLoiterSeconds,
    formTriggerObjectClass,
    setFormTriggerObjectClass,
    formTriggerClapCount,
    setFormTriggerClapCount,
    formTriggerPhrases,
    setFormTriggerPhrases,
    formTriggerPhraseMatch,
    setFormTriggerPhraseMatch,
  } = props;

  return (
    <fieldset className="border border-border rounded-md p-3 space-y-3">
      <legend className="text-xs font-medium text-muted-foreground px-1">
        When should this rule fire
      </legend>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {TRIGGER_TYPES.map((t) => {
          const selected = formTriggerType === t.value;
          const accent = TRIGGER_ACCENTS[t.accent] || TRIGGER_ACCENTS.slate;
          return (
            <button
              key={t.value}
              type="button"
              onClick={() => setFormTriggerType(t.value)}
              className={`relative text-left rounded-md border p-3 transition-all ${
                selected
                  ? `${accent.active} ring-2`
                  : "border-border bg-background hover:bg-muted/60"
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                <t.icon className={selected ? "text-foreground" : "text-muted-foreground"} />
                <span className="text-sm font-medium">{t.label}</span>
                {selected && <span className={`ml-auto w-2 h-2 rounded-full ${accent.dot}`} />}
              </div>
              <div className="text-[11px] text-muted-foreground leading-snug">{t.desc}</div>
            </button>
          );
        })}
      </div>

      {formTriggerType === "object_detected" && (
        <ModelClassPicker
          value={formTriggerLabel}
          onChange={setFormTriggerLabel}
          activeModels={activeModels}
          classes={modelClasses}
          loading={modelClassesLoading}
          anyLabel="Any object"
        />
      )}

      {formTriggerType === "face_recognized" && (
        <div className="space-y-2">
          <label className="text-xs text-muted-foreground block">Person</label>
          {persons.length === 0 ? (
            <p className="text-xs text-muted-foreground px-2 py-3 rounded-md border border-dashed border-border">
              No people yet. Add someone on the People page first.
            </p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-60 overflow-y-auto">
              <button
                type="button"
                onClick={() => setFormTriggerPersonId("")}
                className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                  formTriggerPersonId === ""
                    ? "border-sky-500 bg-sky-500/10 ring-2 ring-sky-500/40"
                    : "border-border bg-background hover:bg-muted/60"
                }`}
              >
                <span className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs text-muted-foreground flex-shrink-0">*</span>
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">Anyone known</div>
                  <div className="text-[10px] text-muted-foreground truncate">Any recognized face</div>
                </div>
              </button>
              {persons.map((p) => {
                const selected = formTriggerPersonId === p.id;
                const initial = (p.display_name || "?").slice(0, 1).toUpperCase();
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setFormTriggerPersonId(p.id)}
                    className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                      selected
                        ? "border-sky-500 bg-sky-500/10 ring-2 ring-sky-500/40"
                        : "border-border bg-background hover:bg-muted/60"
                    }`}
                  >
                    {p.photo_path ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={`/api/files/${p.photo_path}`} alt="" className="w-8 h-8 rounded-full object-cover flex-shrink-0" />
                    ) : (
                      <span className="w-8 h-8 rounded-full bg-sky-500/20 text-sky-300 flex items-center justify-center text-xs font-medium flex-shrink-0">{initial}</span>
                    )}
                    <div className="min-w-0">
                      <div className="text-sm font-medium truncate">{p.display_name}</div>
                      {p.relationship && <div className="text-[10px] text-muted-foreground truncate">{p.relationship}</div>}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
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

      {formTriggerType === "audio_event" && (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Sound type</label>
            <StyledSelect
              value={formTriggerAudioLabel}
              options={AUDIO_LABELS.map((a) => ({ value: a.value, label: a.label }))}
              onChange={setFormTriggerAudioLabel}
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">
              Confidence threshold (0.1 low, 0.7 strict)
            </label>
            <input
              type="number" min="0.05" max="0.95" step="0.05"
              value={formTriggerAudioMinScore}
              onChange={(e) => setFormTriggerAudioMinScore(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-background border border-border text-sm"
            />
          </div>
          <p className="text-[11px] text-muted-foreground">
            Detection runs locally on each camera&apos;s audio track. Needs an RTSP stream that publishes audio.
          </p>
        </div>
      )}

      {formTriggerType === "clap_pattern" && (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Number of claps</label>
            <div className="flex gap-1.5">
              {["2", "3", "4", "5"].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setFormTriggerClapCount(n)}
                  className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                    formTriggerClapCount === n
                      ? "border-rose-500 bg-rose-500/10 text-rose-300"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {n} claps
                </button>
              ))}
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Counts claps that land within ~2s of each other.
            Two claps lights one action, three claps another.
            Needs audio enabled on the camera.
          </p>
        </div>
      )}

      {formTriggerType === "speech_phrase" && (
        <div className="space-y-2">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Phrases to listen for</label>
            <RulePhraseInput
              values={formTriggerPhrases}
              onChange={setFormTriggerPhrases}
              placeholder='e.g. "lights on", "we have a problem"'
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Match mode</label>
            <div className="flex gap-1.5">
              {([
                { v: "any", l: "Any phrase" },
                { v: "all", l: "All phrases" },
              ] as const).map((m) => (
                <button
                  key={m.v}
                  type="button"
                  onClick={() => setFormTriggerPhraseMatch(m.v)}
                  className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
                    formTriggerPhraseMatch === m.v
                      ? "border-rose-500 bg-rose-500/10 text-rose-300"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {m.l}
                </button>
              ))}
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Matches transcript text from the camera&apos;s STT pipeline.
            Case-insensitive substring. Needs audio + transcription enabled.
          </p>
        </div>
      )}

      {(formTriggerType === "loitering" || formTriggerType === "line_cross") && (
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1.5">Pick a camera</label>
            {cameras.length === 0 ? (
              <p className="text-xs text-muted-foreground px-2 py-3 rounded-md border border-dashed border-border">
                No cameras yet. Add one on the Cameras page first.
              </p>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {cameras.map((cam) => {
                  const selected = formTriggerGeomCamId === cam.id;
                  return (
                    <button
                      key={cam.id}
                      type="button"
                      onClick={() => {
                        setFormTriggerGeomCamId(cam.id);
                        setFormTriggerGeomPoints([]);
                      }}
                      className={`flex items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                        selected
                          ? "border-indigo-500 bg-indigo-500/10 ring-2 ring-indigo-500/40"
                          : "border-border bg-background hover:bg-muted/60"
                      }`}
                    >
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        cam.status === "recording" ? "bg-green-500" :
                        cam.status === "online" ? "bg-accent" :
                        "bg-muted-foreground/40"
                      }`} />
                      <span className="text-sm font-medium truncate">{cam.name}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {formTriggerGeomCamId && (() => {
            const cam = cameras.find((c) => c.id === formTriggerGeomCamId);
            if (!cam) return null;
            return (
              <div>
                <label className="text-xs text-muted-foreground block mb-1.5">
                  {formTriggerType === "line_cross"
                    ? "Draw tripwire. Click two points on the feed."
                    : "Draw loiter zone. Click at least three points."}
                </label>
                <GeometryEditor
                  camera={cam}
                  mode={formTriggerType === "line_cross" ? "line" : "polygon"}
                  points={formTriggerGeomPoints}
                  onChange={setFormTriggerGeomPoints}
                />
              </div>
            );
          })()}

          <div>
            <label className="text-xs text-muted-foreground block mb-1">Which objects count (optional)</label>
            <ModelClassPicker
              value={formTriggerObjectClass}
              onChange={setFormTriggerObjectClass}
              activeModels={activeModels}
              classes={modelClasses}
              loading={modelClassesLoading}
              anyLabel="Any tracked object"
            />
          </div>

          {formTriggerType === "loitering" && (
            <div>
              <label className="text-xs text-muted-foreground block mb-1">
                Loiter threshold (seconds inside the zone)
              </label>
              <div className="flex gap-1 flex-wrap">
                {["10", "30", "60", "120", "300"].map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setFormTriggerLoiterSeconds(s)}
                    className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                      formTriggerLoiterSeconds === s
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border hover:bg-muted"
                    }`}
                  >{parseInt(s) >= 60 ? `${Math.round(parseInt(s) / 60)} min` : `${s}s`}</button>
                ))}
                <input
                  type="number"
                  min="1"
                  value={formTriggerLoiterSeconds}
                  onChange={(e) => setFormTriggerLoiterSeconds(e.target.value)}
                  className="w-20 px-2 py-1.5 text-xs rounded border border-border bg-background"
                />
              </div>
            </div>
          )}

          {formTriggerType === "line_cross" && (
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Direction</label>
              <div className="grid grid-cols-3 gap-1">
                {[
                  { v: "any", l: "Either way" },
                  { v: "in", l: "Inbound" },
                  { v: "out", l: "Outbound" },
                ].map((d) => (
                  <button
                    key={d.v}
                    type="button"
                    onClick={() => setFormTriggerLineDirection(d.v)}
                    className={`px-2 py-2 text-xs rounded border transition-colors ${
                      formTriggerLineDirection === d.v
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border hover:bg-muted"
                    }`}
                  >{d.l}</button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </fieldset>
  );
}
