/* Thin fetch helpers against /api/*. Hop endpoints stream NDJSON line-by-line
 * via fetch + ReadableStream; the consumer callback fires for each parsed
 * message as it arrives. */

export type ToolCallHop = {
  kind: "tool_call";
  name: string;
  args: Record<string, unknown>;
};

export type ToolResultHop = {
  kind: "tool_result";
  name: string;
  result: unknown;
};

export type ReplyHop = {
  kind: "reply";
  text: string;
  text_ids: number[];
  dynamic_vars: Record<string, unknown>;
};

// Step-completeness gate: the model tried to reply for a state whose spec entry_tools
// weren't all called yet (e.g. closed a PTP call without recording it) — surfaced
// separately from tool_call/tool_result so the FE can flag it distinctly.
export type WarningHop = {
  kind: "warning";
  text: string;
  // Every detail list is optional: the backend raises warnings from several gates
  // and each carries its own (missing tools, incomplete chain, too many beats,
  // empty slots) — some carry none at all. `text` is the only guaranteed field.
  missing_tools?: string[];
  missing_beats?: string[];
  beats?: string[];
  empty_slots?: string[];
};

export type Hop = ToolCallHop | ToolResultHop | ReplyHop | WarningHop;

export type CustomerData = {
  customer_name?: string;
  loan_type?: string;
  total_amount_due?: number;
  minimum_payment_due?: number;
  due_date?: string;
  due_status?: string;
  customer_phone?: string;
  last_4_digits?: string;
  case_status?: string;
  case_status_note?: string | null;
  company_name?: string;
  agent_name?: string;
  today?: string;
  [k: string]: unknown;
};

export type Agent = "qwen" | "gemini";
/** Engine picked in the ControlBar: the two catalog agents, plus "flow" — the
 *  flow-interpreter path (sft_flow_v1 reading a FlowSpec). "flow" is not an
 *  agent; the server routes it to FlowLiveSession via `?flow=1`. */
export type Engine = Agent | "flow";

/* One persona row from GET /api/cases — flat picker shape (account facts +
 * parsed role-play sections). Mirrors `_persona_summary` on the backend. */
export type PersonaCase = {
  id: string;
  company: string;
  topic: string;
  eval_track: string | null;
  patience: number | null;
  persona: string;
  situation: string;
  constraints: string;
  customer_name?: string;
  loan_type?: string;
  total_amount_due?: number;
  minimum_payment_due?: number;
  due_date?: string;
  due_status?: string;
  customer_phone?: string;
  last_4_digits?: string;
  case_status?: string;
  case_status_note?: string | null;
};

export type VoiceGender = "M" | "F";
// What the voice control is set to. "OFF" is a UI-only mode — the server still
// gets a real gender (the reply text's ครับ/ค่ะ particles depend on it); only
// synthesis is skipped, so a text-only run costs no TTS round-trip.
export type VoiceMode = VoiceGender | "OFF";

/** Thai display names for known company codes (fallback = the code itself). */
export const COMPANY_LABELS: Record<string, string> = {
  AEON: "อิอ้อน",
  JAI: "เจ มันนี่",
  KS: "อยุธยา แคปปิตอล",
  AIS: "เอไอเอส",
};

export type StreamSessionMsg = {
  type: "session";
  session_id: string;
  mode: "replay" | "live";
  case_id: string;
  agent: Engine | null;
  voice_gender: VoiceGender;
  customer_data: CustomerData;
};
export type StreamHopMsg = { type: "hop"; hop: Hop };
export type StreamDoneMsg = {
  type: "done";
  session_done: boolean;
  /** Sum of per-hop LLM-call wall-times for the turn (live turns only; null in
   *  replay or session-only streams). */
  llm_ms?: number | null;
  /** Number of LLM round-trips (tool-call hops) the turn took. */
  llm_hops?: number | null;
};
export type StreamMsg = StreamSessionMsg | StreamHopMsg | StreamDoneMsg;

export type StreamHandlers = {
  onSession?: (m: StreamSessionMsg) => void;
  onHop?: (m: StreamHopMsg) => void;
  onDone?: (m: StreamDoneMsg) => void;
};

async function consumeNdjson(
  resp: Response,
  handlers: StreamHandlers,
): Promise<void> {
  if (!resp.ok || !resp.body) {
    throw new Error(`stream ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buf += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const raw = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!raw) continue;
        try {
          const msg = JSON.parse(raw) as StreamMsg;
          if (msg.type === "session") handlers.onSession?.(msg);
          else if (msg.type === "hop") handlers.onHop?.(msg);
          else if (msg.type === "done") handlers.onDone?.(msg);
        } catch {
          /* skip malformed line */
        }
      }
    }
    if (done) break;
  }
  if (buf.trim()) {
    try {
      const msg = JSON.parse(buf.trim()) as StreamMsg;
      if (msg.type === "session") handlers.onSession?.(msg);
      else if (msg.type === "hop") handlers.onHop?.(msg);
      else if (msg.type === "done") handlers.onDone?.(msg);
    } catch {
      /* noop */
    }
  }
}

export async function fetchCases(): Promise<PersonaCase[]> {
  const resp = await fetch("/api/cases");
  if (!resp.ok) throw new Error(`/api/cases ${resp.status}`);
  return (await resp.json()) as PersonaCase[];
}

/** Company codes that have a FlowSpec (drives the flow-supported set). */
export async function fetchFlowCompanies(): Promise<string[]> {
  const resp = await fetch("/api/flow/companies");
  if (!resp.ok) throw new Error(`/api/flow/companies ${resp.status}`);
  return (await resp.json()) as string[];
}

export type FlowCompanyMeta = {
  company: string;
  display_name: string;
  /** Builder-created companies can be removed; the four shipped ones cannot. */
  deletable: boolean;
};

/** Company code + label + whether the server will let it be deleted. */
export async function fetchFlowCompaniesMeta(): Promise<FlowCompanyMeta[]> {
  const resp = await fetch("/api/flow/companies/meta");
  if (!resp.ok) throw new Error(`/api/flow/companies/meta ${resp.status}`);
  return (await resp.json()) as FlowCompanyMeta[];
}

/** Remove a Builder-created company (spec, catalog, personas, registry entry). */
export async function deleteFlowCompany(
  company: string,
): Promise<{ ok: boolean; removed?: string[]; errors?: string[] }> {
  const resp = await fetch(`/api/flow/company/${encodeURIComponent(company)}`, {
    method: "DELETE",
  });
  return (await resp.json()) as { ok: boolean; removed?: string[]; errors?: string[] };
}

export type FlowBeat = {
  fine_state: string;
  phase: string; // opening | main | close | faq | aux
  label: string; // Thai description of what the line does
  required: boolean;
  hint: string;
  example: string;
};

/** Base-flow beats for the Flow Builder form (fine_state + hint + AEON example). */
export async function fetchFlowBeats(): Promise<FlowBeat[]> {
  const resp = await fetch("/api/flow/beats");
  if (!resp.ok) throw new Error(`/api/flow/beats ${resp.status}`);
  return (await resp.json()) as FlowBeat[];
}

export async function fetchCueLibrary(): Promise<Record<string, string[]>> {
  const resp = await fetch("/api/flow/cue-library");
  if (!resp.ok) throw new Error(`/api/flow/cue-library ${resp.status}`);
  return (await resp.json()) as Record<string, string[]>;
}

export type CreateFlowResult = {
  ok: boolean;
  company?: string;
  case_id?: string;
  beats?: number;
  errors?: string[];
};

// A FlowSpec is a state machine; these are the shapes the editor touches.
export type FlowTemplateRef = { fine_state: string; when_event?: string; optional?: boolean };
export type FlowTransition = { event: string; to: string; tools?: string[]; note?: string };
export type FlowState = {
  id: string;
  phase?: string;
  initial?: boolean;
  terminal?: boolean;
  templates?: FlowTemplateRef[];
  entry_tools?: string[];
  on?: FlowTransition[];
  outcome?: { result: string; reasons?: string[] };
  note?: string;
  [k: string]: unknown;
};
export type FlowSpec = {
  flow_id?: string;
  company?: string;
  events?: Record<string, { desc?: string; cues?: string[] }>;
  states: FlowState[];
  [k: string]: unknown;
};
export type FlowSpecData = {
  company: string;
  spec: FlowSpec;
  fine_states: string[];
  templates?: Record<string, string[]>;
  tools: string[];
};

export async function fetchFlowInstruction(company: string): Promise<string> {
  const resp = await fetch(`/api/flow/instruction?company=${encodeURIComponent(company)}`);
  if (!resp.ok) throw new Error(`/api/flow/instruction ${resp.status}`);
  return ((await resp.json()) as { instruction: string }).instruction;
}

export async function fetchFlowSpec(company: string): Promise<FlowSpecData> {
  const resp = await fetch(`/api/flow/spec?company=${encodeURIComponent(company)}`);
  if (!resp.ok) throw new Error(`/api/flow/spec ${resp.status}`);
  return (await resp.json()) as FlowSpecData;
}

export async function saveFlowSpec(
  company: string,
  spec: FlowSpec,
  newTemplates: { fine_state: string; template: string }[] = [],
): Promise<{ ok: boolean; errors?: string[] }> {
  const resp = await fetch("/api/flow/spec", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ company, spec, new_templates: newTemplates }),
  });
  return (await resp.json()) as { ok: boolean; errors?: string[] };
}

/** Author a new flow company. 400 (validation) comes back as {ok:false, errors}. */
export async function createFlowCompany(body: {
  company: string;
  display_name: string;
  agent_name: string;
  templates: Record<string, string>;
  custom?: { fine_state: string; phase: string; template: string }[];
}): Promise<CreateFlowResult> {
  const resp = await fetch("/api/flow/company", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as CreateFlowResult;
}

/** Author a new flow company from RAW spec+catalog JSON (JSON-editor path, no prefill). */
export async function createFlowCompanyRaw(body: {
  spec: unknown;
  catalog: unknown;
  display_name?: string;
  agent_name?: string;
}): Promise<CreateFlowResult> {
  const resp = await fetch("/api/flow/company/raw", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as CreateFlowResult;
}

export async function fetchModels(): Promise<{ base: string[]; flow: string[] }> {
  const resp = await fetch("/api/models");
  if (!resp.ok) throw new Error(`/api/models ${resp.status}`);
  return (await resp.json()) as { base: string[]; flow: string[] };
}

export async function fetchFlowVersions(
  company: string,
): Promise<{ versions: string[]; default: string }> {
  const resp = await fetch(`/api/flow/versions?company=${encodeURIComponent(company)}`);
  if (!resp.ok) throw new Error(`/api/flow/versions ${resp.status}`);
  return (await resp.json()) as { versions: string[]; default: string };
}

export async function streamSession(
  handlers: StreamHandlers,
  opts: {
    engine?: Engine;
    caseId?: string;
    voiceGender?: VoiceGender;
    model?: string;
    instructionVersion?: string;
  } = {},
): Promise<void> {
  const qs = new URLSearchParams();
  // The Qwen agent is now a flow-regime GRPO model served via vLLM → route it through
  // the FlowLiveSession (flow=1), same as "flow". Only Gemini uses the legacy agent path.
  if (opts.engine === "flow" || opts.engine === "qwen") qs.set("flow", "1");
  else if (opts.engine) qs.set("agent", opts.engine);
  if (opts.caseId) qs.set("case_id", opts.caseId);
  if (opts.voiceGender) qs.set("gender", opts.voiceGender);
  if (opts.model) qs.set("model", opts.model);
  if (opts.instructionVersion) qs.set("instruction_version", opts.instructionVersion);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const resp = await fetch(`/api/session${suffix}`);
  await consumeNdjson(resp, handlers);
}

export async function streamTurn(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
): Promise<void> {
  const resp = await fetch(`/api/session/${sessionId}/turn`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  await consumeNdjson(resp, handlers);
}

export async function streamReset(
  sessionId: string,
  handlers: StreamHandlers,
): Promise<void> {
  const resp = await fetch(`/api/session/${sessionId}/reset`, {
    method: "POST",
  });
  await consumeNdjson(resp, handlers);
}

/** Fire the agent's proactive opening greeting (outbound call — bot speaks
 *  first). Hops stream through the same handler pipeline as a normal turn. */
export async function streamOpening(
  sessionId: string,
  handlers: StreamHandlers,
): Promise<void> {
  const resp = await fetch(`/api/session/${sessionId}/opening`, {
    method: "POST",
  });
  await consumeNdjson(resp, handlers);
}

export type SaveResult = {
  saved: boolean;
  path?: string;
  turns?: number;
  reason?: string;
};

/** Persist the current live conversation server-side, with an optional tester
 * note attached to the saved trajectory. The backend's 400 "nothing to save"
 * is returned as a normal `{ saved: false }`, not an error. */
export async function saveTrajectory(
  sessionId: string,
  comment?: string,
): Promise<SaveResult> {
  const resp = await fetch(`/api/session/${sessionId}/save`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ comment: comment ?? "" }),
  });
  if (resp.status === 400) return (await resp.json()) as SaveResult;
  if (!resp.ok) throw new Error(`/api/session/${sessionId}/save ${resp.status}`);
  return (await resp.json()) as SaveResult;
}

/* ทุก pre-script ของบริษัท + ผูกกับ state ไหน (หน้าอ่าน pre-script) */
export type PrescriptEntry = {
  text_id: number | string; fine_state: string; template: string;
  state: string; phase: string; bound: boolean; intent_name?: string; category?: string;
};
export type PrescriptData = {
  company: string; display_name: string; version: string;
  catalog_file: string; spec_file: string;
  states: { state: string; phase: string; note?: string; beats: string[] }[];
  entries: PrescriptEntry[];
  counts: { templates: number; bound: number; fine_states: number };
};
export async function fetchPrescripts(company: string, version?: string): Promise<PrescriptData> {
  const qs = new URLSearchParams({ company });
  if (version) qs.set("version", version);
  const resp = await fetch(`/api/flow/prescripts?${qs}`);
  if (!resp.ok) throw new Error(`/api/flow/prescripts ${resp.status}`);
  return resp.json();
}
