/**
 * UI strings, in the language the demo is being run in.
 *
 * Deliberately a plain table and a `t()` rather than an i18n library: there are
 * two languages and fewer than sixty strings, and a library would add a build
 * dependency, a provider, and a lazy-loading story for something a Map answers.
 *
 * Three rules keep it honest:
 *
 * - **Thai is the source.** Every key lists Thai first, and `t()` falls back to it
 *   when an English string is missing, so a half-translated build shows Thai rather
 *   than a key name or a blank.
 * - **The language of the UI is the language of the call.** One switch, not two:
 *   picking English on the landing page changes the chrome AND what the agent
 *   speaks, because a Thai interface around an English call reads as a bug.
 * - **It is module state, read through a hook.** `useLang()` subscribes, so the
 *   switch re-renders the tree; components never cache the string.
 */

export type Lang = "th" | "en";

const STORAGE_KEY = "aax6.lang";

function initial(): Lang {
  if (typeof window === "undefined") return "th";
  const saved = window.localStorage?.getItem(STORAGE_KEY);
  return saved === "en" || saved === "th" ? saved : "th";
}

let current: Lang = initial();
const listeners = new Set<() => void>();

// Publish the restored choice IMMEDIATELY, not only when the switch is clicked.
// sttSocket.ts and speech.ts read `window.__aax6Lang` to choose a recogniser, and
// they read it on the first mic connect — which can happen before anyone touches
// the switch (a reload with "en" already saved). Left unset, the page looked
// English while the microphone went to the Thai recogniser, which answers an
// English utterance with an empty transcript and no error at all.
if (typeof window !== "undefined") {
  (window as unknown as { __aax6Lang?: Lang }).__aax6Lang = current;
  document.documentElement.lang = current;
}

export function getLang(): Lang {
  return current;
}

export function setLangGlobal(next: Lang): void {
  if (next === current) return;
  current = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage?.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode — the choice just does not persist */
    }
    // sttSocket.ts and speech.ts read this to pick a recogniser; they are not
    // React and cannot take a prop.
    (window as unknown as { __aax6Lang?: Lang }).__aax6Lang = next;
    // TTS reads its locale from a module variable in audio.ts; pushing it here
    // means a caller cannot change the language and forget the voice.
    void import("./audio").then((a) => a.setLang(next)).catch(() => {});
    document.documentElement.lang = next;
  }
  listeners.forEach((fn) => fn());
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

type Entry = { th: string; en: string };

const S = {
  // landing / mode select
  pickCompany: { th: "เลือกบริษัทที่จะทำงานด้วย", en: "Choose a company to work with" },
  newCompany: { th: "สร้างบริษัทใหม่", en: "Create a new company" },
  company: { th: "บริษัท", en: "Company" },
  whatToDo: { th: "จะทำอะไรกับ {label}", en: "What would you like to do with {label}?" },
  playgroundBlurb: {
    th: "คุยกับบอทเหมือนโทรจริง — เลือกลูกค้า, model version, เสียง แล้วทดสอบ flow",
    en: "Talk to the bot as if it were a real call — pick a customer, a model version and a voice, then test the flow",
  },
  enterPlayground: { th: "เข้า playground →", en: "Enter the playground →" },
  readInstruction: { th: "อ่าน instruction", en: "Read the instruction" },
  instructionBlurb: {
    th: "ดู prompt ที่โมเดลอ่านจริง — render สดจาก flow (JSON) ปัจจุบัน",
    en: "See the prompt the model actually reads — rendered live from the current flow (JSON)",
  },
  open: { th: "เปิดดู →", en: "Open →" },
  backMenu: { th: "← เมนู", en: "← Menu" },
  backMenuTitle: { th: "กลับเมนู", en: "Back to the menu" },

  // company delete
  deleteCompany: { th: "ลบบริษัท {co}", en: "Delete {co}" },
  deleting: { th: "กำลังลบ…", en: "Deleting…" },
  confirmDelete: { th: "ลบเลย?", en: "Delete it?" },
  deleteFailed: { th: "ลบ {co} ไม่สำเร็จ", en: "Could not delete {co}" },
  personasOf: { th: "personas ของ {company}", en: "{company} personas" },

  // control bar
  pickCheckpoint: { th: "เลือก checkpoint ที่ vLLM เสิร์ฟ", en: "Pick a checkpoint vLLM is serving" },
  langThTitle: {
    th: "ภาษาไทย — สคริปต์ เสียง และการถอดเสียงเป็นไทยทั้งหมด",
    en: "Thai — the script, the voice and the speech recognition are all Thai",
  },
  langEnTitle: {
    th: "English — สคริปต์ภาษาอังกฤษของบริษัทนั้น เสียงอังกฤษ และการถอดเสียงอังกฤษ",
    en: "English — the tenant's English script, an English voice, and English speech recognition",
  },
  textOnly: {
    th: "ข้อความอย่างเดียว — ไม่สังเคราะห์เสียง เทสได้เร็วขึ้น",
    en: "Text only — no speech synthesis, which makes testing quicker",
  },
  newCompanyForFlow: { th: "สร้างบริษัทใหม่สำหรับ flow mode", en: "Create a new company for flow mode" },

  // end-of-call card
  hideEnded: { th: "ซ่อน (สายจบแล้ว)", en: "Hide (the call has ended)" },
  hideEndedCard: { th: "ซ่อนกล่องสายจบ", en: "Hide the end-of-call card" },

  // instruction modal
  promptTitle: { th: "prompt ที่โมเดลอ่าน", en: "the prompt the model reads" },
  promptBlurb: {
    th: "นี่คือ instruction ที่ render สดจาก FlowSpec (แก้ใน Edit flow แล้วอันนี้เปลี่ยนตาม) ·",
    en: "This instruction is rendered live from the FlowSpec — edit the flow and this follows ·",
  },
  filledAtCall: { th: "เติมค่าจริงตอนโทร", en: "filled in with real values on the call" },
  readOnly: { th: "อ่านอย่างเดียว · แก้ที่ Edit flow", en: "Read-only · edit it in Edit flow" },
  close: { th: "ปิด", en: "Close" },
  copy: { th: "คัดลอก", en: "Copy" },
  copied: { th: "คัดลอกแล้ว", en: "Copied" },
  loading: { th: "กำลังโหลด…", en: "Loading…" },
  instructionFailed: {
    th: "โหลด instruction ของ {company} ไม่สำเร็จ",
    en: "Could not load the instruction for {company}",
  },

  // flow upload modal
  uploadTitle: { th: "สร้างบริษัทใหม่", en: "Create a new company" },
  uploadSteps: {
    th: "ดาวน์โหลด template → กรอก → อัปโหลด",
    en: "Download the template → fill it in → upload",
  },
  uploadStep1: {
    th: "ดาวน์โหลด template (FlowSpec + catalog เปล่า) — tools เป็น HTTP webhook (ยิง API)",
    en: "Download the template (an empty FlowSpec + catalog) — tools are HTTP webhooks that call your API",
  },
  downloadTemplate: { th: "ดาวน์โหลด template", en: "Download the template" },
  uploadStep2: {
    th: "กรอก states / catalog / tools (url+body) ในไฟล์ JSON",
    en: "Fill in states / catalog / tools (url + body) in the JSON file",
  },
  uploadStep3: {
    th: "อัปโหลดกลับ — ระบบ validate แล้วสร้างบริษัทให้",
    en: "Upload it back — it is validated and the company is created",
  },
  uploadJson: { th: "อัปโหลด JSON", en: "Upload JSON" },
  creating: { th: "กำลังสร้าง…", en: "Creating…" },
  templateFailed: { th: "โหลด template ไม่สำเร็จ: {err}", en: "Could not load the template: {err}" },
  notJson: { th: "ไฟล์ไม่ใช่ JSON ที่ถูกต้อง: {err}", en: "That file is not valid JSON: {err}" },
  createFailed: { th: "สร้างไม่สำเร็จ", en: "Could not create it" },
  uploadFailed: { th: "อัปโหลดไม่สำเร็จ: {err}", en: "Upload failed: {err}" },
} satisfies Record<string, Entry>;

export type Key = keyof typeof S;

export function t(key: Key, vars?: Record<string, string | number>): string {
  const entry = S[key] as Entry;
  let s = (current === "en" ? entry.en : entry.th) || entry.th;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}
