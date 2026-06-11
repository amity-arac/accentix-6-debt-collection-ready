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

export type Hop = ToolCallHop | ToolResultHop | ReplyHop;

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

export type StreamSessionMsg = {
  type: "session";
  session_id: string;
  mode: "replay" | "live";
  case_id: string;
  agent: Agent | null;
  customer_data: CustomerData;
};
export type StreamHopMsg = { type: "hop"; hop: Hop };
export type StreamDoneMsg = { type: "done"; session_done: boolean };
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

export async function streamSession(
  handlers: StreamHandlers,
  opts: { agent?: Agent; caseId?: string } = {},
): Promise<void> {
  const qs = new URLSearchParams();
  if (opts.agent) qs.set("agent", opts.agent);
  if (opts.caseId) qs.set("case_id", opts.caseId);
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
