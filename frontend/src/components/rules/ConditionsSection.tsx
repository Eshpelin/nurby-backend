"use client";

import type { Camera } from "./types";

export interface ConditionsSectionProps {
  cameras: Camera[];
  formCondCameras: string[];
  setFormCondCameras: (v: string[]) => void;
  formScheduleMode: "always" | "custom";
  setFormScheduleMode: (v: "always" | "custom") => void;
  formCondDays: string[];
  setFormCondDays: (updater: string[] | ((prev: string[]) => string[])) => void;
  formCondTimeAfter: string;
  setFormCondTimeAfter: (v: string) => void;
  formCondTimeBefore: string;
  setFormCondTimeBefore: (v: string) => void;
  formCondConfidence: string;
  setFormCondConfidence: (v: string) => void;
}

export function ConditionsSection(props: ConditionsSectionProps) {
  const {
    cameras,
    formCondCameras,
    setFormCondCameras,
    formScheduleMode,
    setFormScheduleMode,
    formCondDays,
    setFormCondDays,
    formCondTimeAfter,
    setFormCondTimeAfter,
    formCondTimeBefore,
    setFormCondTimeBefore,
    formCondConfidence,
    setFormCondConfidence,
  } = props;

  return (
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
                          : [...prev, day.value],
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
  );
}
