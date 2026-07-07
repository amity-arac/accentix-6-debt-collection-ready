/* Per-turn voice-pipeline latency store (client-side timeline).
 *
 * The four customer-facing numbers — VAD, STT, LLM, TTS — are born on three
 * different transports (the /api/stt WebSocket, the /turn NDJSON stream, and the
 * /api/tts audio response). The browser is the only place that observes all
 * four, so this module stitches one per-turn record together by temporal
 * ordering. That ordering is a safe correlation key because turns are strictly
 * serial: App.tsx's busyRef blocks a second turn until the current one's stream
 * ends, and audio drains one clip at a time.
 *
 * Three of the numbers are SERVER-measured and arrive on the wire (the client
 * just collects them): VAD = `endpoint_ms` on speech_end, STT = `recognize_ms`
 * on stt_final, LLM = `llm_ms` on the done line. TTS is CLIENT-measured —
 * perceived time-to-first-audio (audio request → the <audio> `playing` event) —
 * because /api/tts is a raw audio stream whose synth time can't ride a header.
 *
 * Limitations (surface these in the UI): the LLM number is the server's summed
 * per-hop model time, while the end-to-end headline also includes FastAPI /
 * to_thread bridge overhead; STT recognize_ms excludes the WebSocket leg; TTS is
 * first-audible (network + Opus decode included) and may reflect a prefetch
 * cache hit; stages overlap (TTS prefetch overlaps the LLM), so the four numbers
 * are per-stage dead-times, NOT strictly additive to wall-clock.
 */

export type Cache = "hit" | "miss";

export type TurnLatency = {
  seq: number;
  viaMic: boolean;
  // Headline numbers (ms). null = not yet known (pending) or N/A.
  vadMs: number | null; // server endpoint_ms (trailing-silence dead-time)
  sttMs: number | null; // server recognize_ms (batch gRPC)
  llmMs: number | null; // server total_ms (sum of per-hop model calls)
  ttsMs: number | null; // client first-audio (request → playing)
  // Detail (expandable panel).
  llmHops: number | null; // number of LLM round-trips this turn
  sttPerceivedMs: number | null; // client: stt_final − speech_end (incl. WS leg)
  llmTtftMs: number | null; // client: first hop − turn POST (incl. bridge)
  endToEndMs: number | null; // client: first audio − (speech_end | turn POST)
};

export type LatencySnapshot = {
  current: TurnLatency | null; // the in-flight or just-completed turn
  history: TurnLatency[]; // prior completed turns, oldest→newest, capped
};

const HISTORY_CAP = 20;

let seqCounter = 0;
let current: TurnLatency | null = null;
let history: TurnLatency[] = [];
let snapshot: LatencySnapshot = { current: null, history: [] };

const subscribers = new Set<() => void>();

// Perf-clock marks for the in-flight turn (not surfaced; used to compute deltas).
type Marks = {
  tTurnPost: number | null;
  tAnchor: number | null; // mic: speech_end; typed: turn POST — basis for end-to-end
  tTtsReq: number | null;
  firstHopMarked: boolean;
  ttsMarked: boolean;
};
function blankMarks(): Marks {
  return {
    tTurnPost: null,
    tAnchor: null,
    tTtsReq: null,
    firstHopMarked: false,
    ttsMarked: false,
  };
}
let marks: Marks = blankMarks();

// Mic capture stamps filled by the STT WebSocket BEFORE the turn POST fires.
// Consumed (and cleared) when markTurnPost(viaMic=true) promotes them.
type PendingMic = {
  tSpeechEnd: number | null;
  vadMs: number | null;
  sttMs: number | null;
  sttPerceivedMs: number | null;
};
let pendingMic: PendingMic | null = null;

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}
function round(x: number): number {
  return Math.round(x);
}
function publish(): void {
  snapshot = { current: current ? { ...current } : null, history };
  subscribers.forEach((fn) => {
    try {
      fn();
    } catch {
      /* a bad subscriber shouldn't break telemetry */
    }
  });
}

export function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}
export function getSnapshot(): LatencySnapshot {
  return snapshot;
}

// ---- STT WebSocket events (mic capture, before the turn POST) ----

export function markSpeechBegin(): void {
  pendingMic = { tSpeechEnd: null, vadMs: null, sttMs: null, sttPerceivedMs: null };
}

export function markSpeechEnd(endpointMs: number | null): void {
  if (!pendingMic) {
    pendingMic = { tSpeechEnd: null, vadMs: null, sttMs: null, sttPerceivedMs: null };
  }
  pendingMic.tSpeechEnd = now();
  pendingMic.vadMs = endpointMs;
}

export function markSttFinal(recognizeMs: number | null): void {
  if (!pendingMic) return;
  pendingMic.sttMs = recognizeMs;
  if (pendingMic.tSpeechEnd != null) {
    pendingMic.sttPerceivedMs = round(now() - pendingMic.tSpeechEnd);
  }
}

// ---- Turn (NDJSON) events ----

export function markTurnPost(viaMic: boolean): void {
  // Roll the previous turn into history before starting a new one.
  if (current) history = [...history, current].slice(-HISTORY_CAP);

  seqCounter += 1;
  current = {
    seq: seqCounter,
    viaMic,
    vadMs: viaMic ? pendingMic?.vadMs ?? null : null,
    sttMs: viaMic ? pendingMic?.sttMs ?? null : null,
    sttPerceivedMs: viaMic ? pendingMic?.sttPerceivedMs ?? null : null,
    llmMs: null,
    ttsMs: null,
    llmHops: null,
    llmTtftMs: null,
    endToEndMs: null,
  };
  marks = blankMarks();
  marks.tTurnPost = now();
  // End-to-end basis: when the caller stopped talking (mic) or hit send (typed).
  marks.tAnchor = viaMic ? pendingMic?.tSpeechEnd ?? marks.tTurnPost : marks.tTurnPost;
  pendingMic = null;
  publish();
}

export function markFirstHop(): void {
  if (!current || marks.firstHopMarked) return;
  marks.firstHopMarked = true;
  if (marks.tTurnPost != null) current.llmTtftMs = round(now() - marks.tTurnPost);
  publish();
}

export function markDone(llmMs: number | null, llmHops: number | null): void {
  if (!current) return;
  if (llmMs != null) current.llmMs = llmMs;
  if (llmHops != null) current.llmHops = llmHops;
  publish();
}

// ---- TTS (audio) events ----

/** First clip of the turn only — the time-to-first-audio basis. On a tool turn
 *  that first clip is the spoken "please wait" filler (the server relabels the
 *  pending tool_call into a spoken reply hop, see sessions.py), so this "TTS"
 *  reading is the filler's client first-audio, not the substantive reply. */
export function markTtsRequest(): void {
  if (!current || marks.tTtsReq != null) return;
  marks.tTtsReq = now();
}

export function markTtsPlaying(): void {
  if (!current || marks.ttsMarked) return;
  marks.ttsMarked = true;
  const t = now();
  if (marks.tTtsReq != null) current.ttsMs = round(t - marks.tTtsReq);
  if (marks.tAnchor != null) current.endToEndMs = round(t - marks.tAnchor);
  publish();
}

/** Clear all timing — called on session reset/start so the readout starts fresh. */
export function reset(): void {
  current = null;
  history = [];
  pendingMic = null;
  marks = blankMarks();
  publish();
}
