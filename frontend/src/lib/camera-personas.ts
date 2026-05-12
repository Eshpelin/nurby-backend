/**
 * Camera persona presets.
 *
 * A persona is a bundle of camera config that captures a common
 * use-case in one click. Picking a persona just sets fields. The user
 * can override anything afterward. None of these are required.
 *
 * Add new personas here. They show up in the camera-edit and
 * add-camera UI automatically.
 */

export interface PersonaPatch {
  // Detection
  detect_objects?: boolean;
  detect_faces?: boolean;
  scene_mode?: "indoor" | "outdoor";
  object_confidence?: number;
  detection_models?: { model: string; confidence: number; enabled: boolean; label_filter: string[] }[];
  yolo_world_prompts?: string[];
  privacy_zone_targets?: string[];
  // VLM
  vlm_trigger?: "always" | "on_object";
  vlm_trigger_objects?: string[];
  vlm_max_tokens?: number;
  // Recording
  recording_mode?: "off" | "always" | "on_motion" | "on_object" | "clip";
  recording_trigger_objects?: string[];
  recording_clip_pre?: number;
  recording_clip_post?: number;
  // Retention
  retention_mode?: "none" | "time" | "size";
  retention_days?: number;
  retention_gb?: number;
  // Audio
  audio_capture_enabled?: boolean;
  audio_transcribe_enabled?: boolean;
  audio_store_raw?: boolean;
  audio_retention_days?: number;
  transcript_retention_days?: number;
  // Summaries
  summary_mode?: "off" | "periodic" | "event" | "both";
  summary_period_seconds?: number;
  summary_event_quiet_seconds?: number;
  summary_event_trigger_objects?: string[];
  summary_event_min_duration_seconds?: number;
  // Conversations
  conversation_gap_seconds?: number;
  conversation_summary_enabled?: boolean;
}

export interface Persona {
  id: string;
  label: string;
  hint: string;
  iconPath: string; // svg `d` for a 24x24 icon
  patch: PersonaPatch;
}

export const CAMERA_PERSONAS: Persona[] = [
  {
    id: "front-door",
    label: "Front Door",
    hint: "Person and package detection. Records when someone arrives. Event recap when a visit closes.",
    iconPath: "M3 21h18M5 21V7l7-4 7 4v14M9 21V12h6v9",
    patch: {
      detect_objects: true,
      detect_faces: true,
      scene_mode: "outdoor",
      object_confidence: 0.4,
      detection_models: [
        { model: "yolov8x-worldv2.pt", confidence: 0.4, enabled: true, label_filter: [] },
      ],
      yolo_world_prompts: [
        "person", "package", "delivery driver", "mail truck",
        "bicycle", "stroller", "dog", "cat", "weapon",
      ],
      vlm_trigger: "on_object",
      vlm_trigger_objects: ["person", "package", "delivery driver"],
      vlm_max_tokens: 200,
      recording_mode: "on_object",
      recording_trigger_objects: ["person"],
      recording_clip_pre: 5,
      recording_clip_post: 15,
      retention_mode: "time",
      retention_days: 30,
      summary_mode: "event",
      summary_event_quiet_seconds: 60,
      summary_event_trigger_objects: ["person"],
      summary_event_min_duration_seconds: 5,
      audio_capture_enabled: true,
      audio_transcribe_enabled: true,
      conversation_gap_seconds: 30,
      conversation_summary_enabled: true,
      privacy_zone_targets: ["window"],
    },
  },
  {
    id: "baby-cam",
    label: "Baby Cam",
    hint: "Continuous recording. Audio capture for cries. Periodic recap of the room.",
    iconPath:
      "M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1-8 0V7a4 4 0 0 1 4-4zM6 21v-2a6 6 0 0 1 12 0v2",
    patch: {
      detect_objects: true,
      detect_faces: true,
      scene_mode: "indoor",
      object_confidence: 0.3,
      detection_models: [
        { model: "yolov8n.pt", confidence: 0.3, enabled: true, label_filter: [] },
      ],
      vlm_trigger: "always",
      vlm_max_tokens: 200,
      recording_mode: "always",
      recording_clip_pre: 0,
      recording_clip_post: 0,
      retention_mode: "time",
      retention_days: 7,
      audio_capture_enabled: true,
      audio_transcribe_enabled: true,
      audio_store_raw: true,
      audio_retention_days: 7,
      transcript_retention_days: 30,
      summary_mode: "periodic",
      summary_period_seconds: 1800,
      summary_event_trigger_objects: ["person"],
      conversation_gap_seconds: 45,
      conversation_summary_enabled: true,
    },
  },
  {
    id: "pet-cam",
    label: "Pet Cam",
    hint: "Cat and dog triggers. No face recognition. Event recap per pet visit.",
    iconPath:
      "M4 8a3 3 0 1 1 3 3M17 8a3 3 0 1 0-3 3M9 13a3 3 0 1 1-3 3M15 13a3 3 0 1 0 3 3M12 14c-3 0-5 2-5 4s2 3 5 3 5-1 5-3-2-4-5-4z",
    patch: {
      detect_objects: true,
      detect_faces: false,
      scene_mode: "indoor",
      object_confidence: 0.3,
      detection_models: [
        { model: "yolov8n.pt", confidence: 0.3, enabled: true, label_filter: [] },
      ],
      vlm_trigger: "on_object",
      vlm_trigger_objects: ["cat", "dog", "bird"],
      vlm_max_tokens: 150,
      recording_mode: "on_object",
      recording_trigger_objects: ["cat", "dog"],
      recording_clip_pre: 3,
      recording_clip_post: 10,
      retention_mode: "time",
      retention_days: 14,
      summary_mode: "event",
      summary_event_quiet_seconds: 90,
      summary_event_trigger_objects: ["cat", "dog"],
      summary_event_min_duration_seconds: 3,
      audio_capture_enabled: false,
      audio_transcribe_enabled: false,
      conversation_summary_enabled: false,
    },
  },
  {
    id: "wildlife",
    label: "Wildlife",
    hint: "Animal detection (deer, bear, coyote, bird). Outdoor scene mode. Generous storage.",
    iconPath:
      "M4 12c0-4 3-7 8-7s8 3 8 7-3 8-8 8c-3 0-5-1-7-3M9 8l-2-3M15 8l2-3",
    patch: {
      detect_objects: true,
      detect_faces: false,
      scene_mode: "outdoor",
      object_confidence: 0.35,
      // OIV7 has the long-tail wildlife labels (bear, deer, raccoon).
      detection_models: [
        { model: "yolov8s-oiv7.pt", confidence: 0.35, enabled: true, label_filter: [] },
      ],
      vlm_trigger: "on_object",
      vlm_trigger_objects: ["bird", "cat", "dog", "deer", "bear", "fox", "raccoon", "rabbit", "squirrel"],
      vlm_max_tokens: 200,
      recording_mode: "on_object",
      recording_trigger_objects: ["bird", "cat", "dog", "deer", "bear", "fox", "raccoon"],
      recording_clip_pre: 5,
      recording_clip_post: 15,
      retention_mode: "size",
      retention_gb: 100,
      summary_mode: "event",
      summary_event_quiet_seconds: 120,
      summary_event_trigger_objects: ["deer", "bear", "fox", "coyote", "raccoon"],
      summary_event_min_duration_seconds: 5,
      audio_capture_enabled: false,
      audio_transcribe_enabled: false,
      conversation_summary_enabled: false,
    },
  },
  {
    id: "driveway",
    label: "Driveway",
    hint: "Vehicle and plate detection. Recording on car arrival. Outdoor.",
    iconPath:
      "M3 17h18M5 17l1-5h12l1 5M7 12V8h10v4M8 17v3M16 17v3",
    patch: {
      detect_objects: true,
      detect_faces: false,
      scene_mode: "outdoor",
      object_confidence: 0.4,
      detection_models: [
        { model: "yolov8s.pt", confidence: 0.4, enabled: true, label_filter: [] },
      ],
      vlm_trigger: "on_object",
      vlm_trigger_objects: ["car", "truck", "motorcycle", "bus", "person"],
      vlm_max_tokens: 200,
      recording_mode: "on_object",
      recording_trigger_objects: ["car", "truck", "motorcycle"],
      recording_clip_pre: 5,
      recording_clip_post: 30,
      retention_mode: "time",
      retention_days: 30,
      summary_mode: "event",
      summary_event_quiet_seconds: 90,
      summary_event_trigger_objects: ["car", "truck", "person"],
      summary_event_min_duration_seconds: 5,
      audio_capture_enabled: false,
      audio_transcribe_enabled: false,
      conversation_summary_enabled: false,
    },
  },
];

export function findPersona(id: string): Persona | undefined {
  return CAMERA_PERSONAS.find((p) => p.id === id);
}
