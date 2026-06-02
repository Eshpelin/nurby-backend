/**
 * Camera-brand connection cheat sheets.
 *
 * The single biggest snag for new users is "what's my camera's stream
 * URL and where do I turn on RTSP/ONVIF?" These entries answer that per
 * brand. Each carries one or more RTSP URL templates and the exact
 * clicks to enable RTSP/ONVIF + find credentials.
 *
 * Templates use <user> <pass> <ip> placeholders. The help panel can
 * drop a template straight into the stream-URL field; the user swaps the
 * three placeholders. Credentials may instead be left out of the URL and
 * entered in Nurby's Credentials section.
 *
 * support:
 *   "yes"     RTSP works out of the box or after a setting toggle
 *   "limited" only some models / requires extra firmware or an add-on
 *   "no"      cloud-locked. No direct RTSP without a bridge
 */

export type RtspSupport = "yes" | "limited" | "no";

export interface RtspTemplate {
  label: string; // e.g. "Main stream", "Sub stream"
  url: string; // contains <user> <pass> <ip>
}

export interface CameraBrand {
  id: string;
  name: string;
  support: RtspSupport;
  /** Default RTSP port, shown as a hint. */
  port?: number;
  templates: RtspTemplate[];
  /** Ordered, plain-English steps to get the connection details. */
  steps: string[];
  /** Gotchas worth calling out. */
  notes?: string[];
}

export const CAMERA_BRANDS: CameraBrand[] = [
  {
    id: "hikvision",
    name: "Hikvision",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/Streaming/Channels/101" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/Streaming/Channels/102" },
    ],
    steps: [
      "Find the camera's IP. check your router's device list, or use the Hikvision SADP tool.",
      "Log into the camera's web page at http://<ip> with your admin account.",
      "Go to Configuration → Network → Advanced Settings → Integration Protocol and tick Enable ONVIF, then add an ONVIF user.",
      "RTSP is on by default on port 554. Use your admin username and password.",
    ],
    notes: ["Channel 101 = main (full quality), 102 = sub (lighter). Start with the sub stream on a weak machine."],
  },
  {
    id: "dahua",
    name: "Dahua",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=1" },
    ],
    steps: [
      "Find the camera's IP (router device list, or the Dahua ConfigTool).",
      "Log into http://<ip> with your admin account.",
      "Go to Setting → Network → Port and confirm RTSP port 554; enable ONVIF under Setting → Network → Platform Access if present.",
      "Use your admin username and password.",
    ],
    notes: ["subtype=0 is the main stream, subtype=1 the sub stream."],
  },
  {
    id: "reolink",
    name: "Reolink",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/h264Preview_01_main" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/h264Preview_01_sub" },
    ],
    steps: [
      "Open the Reolink app or client and find the camera's IP under Device Settings → Network → Network Information.",
      "Go to Device Settings → Network → Advanced → Port Settings and enable RTSP and ONVIF.",
      "Use the camera's own username and password (not your Reolink cloud login).",
    ],
    notes: [
      "Battery-powered Reolink models (Argus, etc) often do NOT support RTSP. wired/PoE models do.",
    ],
  },
  {
    id: "amcrest",
    name: "Amcrest",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=1" },
    ],
    steps: [
      "Find the camera's IP (router list, or the Amcrest IP Config tool).",
      "Log into http://<ip> with your admin account.",
      "Setup → Network → Connection. Confirm RTSP port 554. ONVIF is usually on by default.",
      "Use your admin username and password.",
    ],
    notes: ["Amcrest shares Dahua's URL scheme."],
  },
  {
    id: "tapo",
    name: "TP-Link Tapo",
    support: "yes",
    port: 554,
    templates: [
      { label: "HD stream", url: "rtsp://<user>:<pass>@<ip>:554/stream1" },
      { label: "SD stream", url: "rtsp://<user>:<pass>@<ip>:554/stream2" },
    ],
    steps: [
      "In the Tapo app, open the camera → Settings → Advanced Settings → Camera Account.",
      "Create a Camera Account username + password here. THIS is what the RTSP URL uses, not your TP-Link cloud login.",
      "Find the camera IP under Settings → Device Info, or in your router.",
      "Use the Camera Account username and password you just made.",
    ],
    notes: ["The most common Tapo mistake. Using the TP-Link app login instead of the separate Camera Account."],
  },
  {
    id: "wyze",
    name: "Wyze",
    support: "limited",
    port: 8554,
    templates: [
      { label: "RTSP firmware", url: "rtsp://<user>:<pass>@<ip>:8554/live" },
    ],
    steps: [
      "Wyze cameras only do RTSP after flashing the official RTSP firmware (Wyze Cam v2 / v3 / Pan).",
      "In the Wyze app, open the camera → Settings → Advanced Settings → RTSP, enable it, and set a username + password.",
      "The app shows the full rtsp:// URL. copy it here.",
    ],
    notes: ["Stock Wyze firmware has no RTSP. if you don't see the RTSP option, flash the RTSP firmware from Wyze's support site first."],
  },
  {
    id: "unifi",
    name: "Ubiquiti UniFi Protect",
    support: "yes",
    port: 7447,
    templates: [
      { label: "RTSP(S) stream", url: "rtsps://<ip>:7441/<streamId>?enableSrtp" },
    ],
    steps: [
      "Open UniFi Protect → pick the camera → Settings → Advanced → RTSP.",
      "Toggle on one of the quality streams (High / Medium / Low). Protect generates a full rtsps:// URL.",
      "Copy that exact URL here. It already includes the stream id and port.",
    ],
    notes: ["UniFi uses RTSPS (TLS) on port 7441. credentials are embedded in the generated URL, so the Credentials section can stay empty."],
  },
  {
    id: "axis",
    name: "Axis",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>/axis-media/media.amp" },
    ],
    steps: [
      "Find the camera IP (router list, or the AXIS IP Utility).",
      "Log into http://<ip> with your admin account.",
      "RTSP and ONVIF are supported by default. Add an ONVIF user under System → ONVIF if you want a separate account.",
    ],
  },
  {
    id: "foscam",
    name: "Foscam",
    support: "yes",
    port: 88,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:88/videoMain" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:88/videoSub" },
    ],
    steps: [
      "Find the camera IP in the Foscam app under Settings → Device Info, or in your router.",
      "Log into the web UI and enable RTSP/ONVIF under Settings → Network → Port if not already on.",
      "Foscam often uses port 88, sometimes 554. check the Port settings page.",
    ],
    notes: ["Port varies by model. If 88 fails, try 554."],
  },
  {
    id: "lorex",
    name: "Lorex",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=1" },
    ],
    steps: [
      "Find the camera/NVR IP in the Lorex app or on the NVR's network screen.",
      "Enable RTSP/ONVIF in the NVR or camera Network settings.",
      "Use the device's admin username and password. For an NVR, change channel=1 to the channel you want.",
    ],
    notes: ["Lorex is Dahua-based, so it uses the Dahua URL scheme."],
  },
  {
    id: "annke",
    name: "Annke",
    support: "yes",
    port: 554,
    templates: [
      { label: "Hikvision-style", url: "rtsp://<user>:<pass>@<ip>:554/Streaming/Channels/101" },
      { label: "Dahua-style", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" },
    ],
    steps: [
      "Find the camera IP (router list).",
      "Log into the web UI and enable ONVIF under Network → Advanced.",
      "Annke uses either the Hikvision or Dahua URL scheme depending on the model. Try the Hikvision one first.",
    ],
    notes: ["If the Hikvision-style URL fails, the camera is Dahua-based, so use the second template."],
  },
  {
    id: "uniview",
    name: "Uniview (UNV)",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/unicast/c1/s0/live" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/unicast/c1/s1/live" },
    ],
    steps: [
      "Find the camera IP (router list, or Uniview's EZTools / Guard tool).",
      "Log into http://<ip> with your admin account.",
      "Setup → Network → Port. Confirm RTSP 554. ONVIF is on by default; add an ONVIF user under Network → Platform if you want a separate login.",
      "Use your admin username and password. For an NVR, change c1 to the channel number.",
    ],
    notes: ["c1 = channel 1, s0 = main / s1 = sub."],
  },
  {
    id: "hanwha",
    name: "Hanwha Vision / Wisenet (Samsung)",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main (profile1)", url: "rtsp://<user>:<pass>@<ip>:554/profile1/media.smp" },
      { label: "Sub (profile2)", url: "rtsp://<user>:<pass>@<ip>:554/profile2/media.smp" },
    ],
    steps: [
      "Find the camera IP (router list, or the Wisenet Device Manager).",
      "Log into http://<ip> with your admin account.",
      "Setup → Network → ONVIF. add an ONVIF user. RTSP is enabled by default on 554.",
      "Use your admin username and password.",
    ],
    notes: ["Profile names can differ per model. If profile1/2 fail, check Setup → Video profiles for the exact profile name."],
  },
  {
    id: "bosch",
    name: "Bosch",
    support: "yes",
    port: 554,
    templates: [
      { label: "Stream 1", url: "rtsp://<user>:<pass>@<ip>:554/?inst=1" },
      { label: "Stream 2", url: "rtsp://<user>:<pass>@<ip>:554/?inst=2" },
    ],
    steps: [
      "Easiest path. Use the Scan tab above. Bosch cameras are ONVIF-compliant and discover cleanly.",
      "Otherwise log into http://<ip>, go to Configuration → Network → Network Access, and confirm the RTSP port.",
      "Use your camera's service/admin account.",
    ],
    notes: ["Bosch RTSP paths vary by firmware. If the templates fail, the Scan tab + ONVIF is the reliable route."],
  },
  {
    id: "vivotek",
    name: "Vivotek",
    support: "yes",
    port: 554,
    templates: [
      { label: "Stream 1", url: "rtsp://<user>:<pass>@<ip>:554/live.sdp" },
      { label: "Stream 2", url: "rtsp://<user>:<pass>@<ip>:554/live2.sdp" },
    ],
    steps: [
      "Find the camera IP (router list, or Vivotek's Shepherd / IW2 tool).",
      "Log into http://<ip> with your admin account.",
      "Configuration → Network → General confirms RTSP 554; enable ONVIF under Security → ONVIF.",
      "Use your admin username and password.",
    ],
    notes: ["Stream path can be live.sdp, live1s1.sdp, or live2.sdp depending on model."],
  },
  {
    id: "honeywell",
    name: "Honeywell",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/h264" },
      { label: "Channel/stream", url: "rtsp://<user>:<pass>@<ip>:554/ch01/0" },
    ],
    steps: [
      "Use the Scan tab above first. Most Honeywell IP cameras are ONVIF-compliant.",
      "Otherwise log into http://<ip>, confirm the RTSP port under Network settings, and enable ONVIF if present.",
      "Use your admin username and password.",
    ],
    notes: ["Honeywell spans many series (Performance, Equip, 30/35 Series) with different paths. ONVIF/Scan is the safe bet."],
  },
  {
    id: "avigilon",
    name: "Avigilon (Motorola)",
    support: "yes",
    port: 554,
    templates: [
      { label: "Primary", url: "rtsp://<user>:<pass>@<ip>:554/defaultPrimary?streamType=u" },
      { label: "Secondary", url: "rtsp://<user>:<pass>@<ip>:554/defaultSecondary?streamType=u" },
    ],
    steps: [
      "Find the camera IP (router list, or the Avigilon Camera Configuration Tool).",
      "Log into the camera web UI and confirm RTSP/ONVIF are enabled.",
      "Use the camera's admin account. StreamType=u requests UDP.",
    ],
  },
  {
    id: "ipro",
    name: "Panasonic / i-PRO",
    support: "yes",
    port: 554,
    templates: [
      { label: "Stream 1", url: "rtsp://<user>:<pass>@<ip>:554/MediaInput/h264/stream_1" },
      { label: "Stream 2", url: "rtsp://<user>:<pass>@<ip>:554/MediaInput/h264/stream_2" },
    ],
    steps: [
      "Find the camera IP (router list, or the i-PRO Configuration Tool / Easy IP Setup).",
      "Log into http://<ip> with your admin account.",
      "Setup → Network → ONVIF. add an ONVIF user. Confirm RTSP is enabled.",
      "Use your admin username and password.",
    ],
    notes: ["Older Panasonic units use /MediaInput/h264 without the stream suffix."],
  },
  {
    id: "swann",
    name: "Swann",
    support: "yes",
    port: 554,
    templates: [
      { label: "Dahua-style", url: "rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" },
      { label: "Channel-style", url: "rtsp://<user>:<pass>@<ip>:554/ch01/0" },
    ],
    steps: [
      "Find the DVR/NVR or camera IP in the Swann app or on the recorder's network screen.",
      "Enable RTSP/ONVIF in the recorder's Network settings.",
      "Use the device admin account. For an NVR, set channel to the camera you want.",
    ],
    notes: ["Many Swann recorders are Dahua-based. If the Dahua URL fails, try the channel-style path."],
  },
  {
    id: "ezviz",
    name: "EZVIZ",
    support: "limited",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://admin:<verificationcode>@<ip>:554/Streaming/Channels/101" },
      { label: "Sub stream", url: "rtsp://admin:<verificationcode>@<ip>:554/Streaming/Channels/102" },
    ],
    steps: [
      "In the EZVIZ app, open the camera → Settings and turn OFF Image Encryption (or note the encryption is the RTSP password).",
      "The username is admin. The password is the device Verification Code printed on a sticker on the camera.",
      "Find the camera IP in your router. EZVIZ is Hikvision-based, so it uses the Hikvision URL scheme.",
    ],
    notes: [
      "EZVIZ leans cloud-first. Some models only allow RTSP on the local network and only after disabling encryption in the app.",
    ],
  },
  {
    id: "tiandy",
    name: "Tiandy",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/0/main" },
      { label: "Sub stream", url: "rtsp://<user>:<pass>@<ip>:554/0/sub" },
    ],
    steps: [
      "Find the camera IP (router list, or Tiandy's search tool).",
      "Log into the web UI and confirm RTSP/ONVIF under Network settings.",
      "Use your admin username and password.",
    ],
  },
  {
    id: "geovision",
    name: "GeoVision",
    support: "yes",
    port: 554,
    templates: [
      { label: "Channel 1", url: "rtsp://<user>:<pass>@<ip>:554/CH001.sdp" },
    ],
    steps: [
      "Find the camera IP (router list, or the GV-IP Device Utility).",
      "Log into the web UI and confirm RTSP/ONVIF are enabled under Network settings.",
      "Use your admin username and password. Change CH001 to the channel you want.",
    ],
  },
  {
    id: "pelco",
    name: "Pelco",
    support: "yes",
    port: 554,
    templates: [
      { label: "Stream 1", url: "rtsp://<user>:<pass>@<ip>:554/stream1" },
      { label: "Stream 2", url: "rtsp://<user>:<pass>@<ip>:554/stream2" },
    ],
    steps: [
      "Find the camera IP (router list, or the Pelco Device Utility).",
      "Log into the web UI. RTSP/ONVIF are supported on Sarix and newer lines.",
      "Use your admin username and password.",
    ],
  },
  {
    id: "sony",
    name: "Sony",
    support: "yes",
    port: 554,
    templates: [
      { label: "Video 1", url: "rtsp://<user>:<pass>@<ip>:554/media/video1" },
      { label: "Video 2", url: "rtsp://<user>:<pass>@<ip>:554/media/video2" },
    ],
    steps: [
      "Find the camera IP (router list).",
      "Log into the web UI and confirm RTSP/ONVIF are enabled.",
      "Use your admin username and password.",
    ],
    notes: ["Applies to Sony SNC-series network cameras (legacy. Sony exited new camera sales)."],
  },
  {
    id: "acti",
    name: "ACTi",
    support: "yes",
    port: 554,
    templates: [
      { label: "Main stream", url: "rtsp://<user>:<pass>@<ip>:554/" },
    ],
    steps: [
      "Use the Scan tab above first. ACTi cameras are ONVIF-compliant.",
      "Otherwise log into the web UI and confirm the RTSP port (some models use 7070 instead of 554).",
      "Use your admin username and password.",
    ],
    notes: ["RTSP port is 554 on most models but 7070 on some older ones."],
  },
  {
    id: "mobotix",
    name: "Mobotix",
    support: "yes",
    port: 554,
    templates: [
      { label: "H.264 stream", url: "rtsp://<user>:<pass>@<ip>/mobotix.sdp" },
    ],
    steps: [
      "In the Mobotix camera UI, enable the RTSP server under Admin Menu → Integration → RTSP / Multicast.",
      "Find the camera IP (router list, or MxManagementCenter).",
      "Use your admin username and password.",
    ],
    notes: ["RTSP is off by default on Mobotix. You must enable the RTSP server first. ONVIF/Scan also works once enabled."],
  },
  {
    id: "generic_onvif",
    name: "Generic ONVIF / other",
    support: "yes",
    port: 554,
    templates: [
      { label: "Common path", url: "rtsp://<user>:<pass>@<ip>:554/onvif1" },
      { label: "Alt path", url: "rtsp://<user>:<pass>@<ip>:554/stream1" },
    ],
    steps: [
      "Use the Scan network tab above. ONVIF cameras advertise themselves and Nurby can discover the IP + stream URL automatically.",
      "If the scan finds it, just enter the camera's username and password.",
      "If you have to type it manually, check the maker's manual for the RTSP path, then add it after rtsp://<user>:<pass>@<ip>:554/.",
    ],
    notes: ["When in doubt, try the Scan tab first. It removes the guesswork for any ONVIF-compliant camera."],
  },
  {
    id: "cloud_only",
    name: "Ring / Nest / Arlo (cloud)",
    support: "no",
    templates: [],
    steps: [
      "These brands lock the video to their own cloud app and do NOT expose a direct RTSP/ONVIF stream.",
      "To use them with Nurby you need a bridge such as Scrypted, which re-exposes the feed as RTSP. that is an advanced, separate setup.",
      "If you want a plug-and-play camera for Nurby, a wired ONVIF/RTSP camera (Hikvision, Dahua, Reolink PoE, Amcrest, Axis) is the easy path.",
    ],
    notes: ["No native RTSP. consider a Scrypted bridge or a different camera for direct use."],
  },
];

export function findBrand(id: string): CameraBrand | undefined {
  return CAMERA_BRANDS.find((b) => b.id === id);
}
