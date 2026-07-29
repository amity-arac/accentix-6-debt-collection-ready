import { useCallback, useRef, useState } from "react";
import {
  streamSession,
  streamTurn,
  streamReset,
  streamOpening,
  type Engine,
  type CustomerData,
  type Hop,
  type VoiceGender,
} from "../api";
import * as audio from "../audio";
import * as latency from "../latency";

export type BubbleEntry =
  | { id: number; kind: "user"; text: string; viaMic: boolean }
  | {
      id: number;
      kind: "reply";
      text: string;
      text_ids: number[];
      speaking: boolean;
      ttsFailed?: boolean;
    }
  | { id: number; kind: "tool_call"; name: string; args: Record<string, unknown> }
  | { id: number; kind: "tool_result"; name: string; result: unknown };

export type StreamError = { message: string; retry: () => void } | null;

export type SessionState = {
  ready: boolean;
  sessionId: string | null;
  mode: "replay" | "live" | null;
  caseId: string | null;
  agent: Engine;
  serverAgent: Engine | null;
  model: string;                   // "" = backend default (env)
  voiceGender: VoiceGender;        // user's current pick
  serverVoiceGender: VoiceGender | null;  // what the live session was actually built with
  customer: CustomerData;
  bubbles: BubbleEntry[];
  busy: boolean; // a stream is being consumed
  done: boolean;
  paused: boolean;
  streamError: StreamError;
};

let nextId = 1;
const newId = () => nextId++;

export function useSession() {
  const [state, setState] = useState<SessionState>({
    ready: false,
    sessionId: null,
    mode: null,
    caseId: null,
    agent: "qwen",
    serverAgent: null,
    model: "",
    voiceGender: "F",
    serverVoiceGender: null,
    customer: {},
    bubbles: [],
    busy: false,
    done: false,
    paused: false,
    streamError: null,
  });
  // Current live session id, mirrored into a ref so callbacks fired right after
  // start() (e.g. fireOpening) see the fresh id without waiting for a re-render.
  const sessionIdRef = useRef<string | null>(null);
  // Latest user-chosen engine, read inside start() to avoid closure staleness.
  const agentRef = useRef<Engine>("qwen");
  // Latest user-chosen persona/case id (null → backend default). Read inside
  // start() so a fresh session is created for the picked persona.
  const caseIdRef = useRef<string | null>(null);
  // Latest user-chosen TTS voice gender, read inside start(). Independent of
  // the reply text's own grammatical gender — this only picks which Chirp 3 HD
  // voice speaks it (see audio.ts's setVoiceGender).
  const voiceGenderRef = useRef<VoiceGender>("F");
  const modelRef = useRef<string>("");

  const setAgent = useCallback((agent: Engine) => {
    agentRef.current = agent;
    setState((s) => ({ ...s, agent }));
  }, []);

  const setModel = useCallback((model: string) => {
    modelRef.current = model;
    setState((s) => ({ ...s, model }));
  }, []);

  const setVoiceGender = useCallback((voiceGender: VoiceGender) => {
    voiceGenderRef.current = voiceGender;
    audio.setVoiceGender(voiceGender);
    setState((s) => ({ ...s, voiceGender }));
  }, []);

  // Hops arrive over the network at their own pace. Render each one the moment
  // it arrives (tool_call / tool_result / reply bubbles never wait on anything)
  // and play reply TTS on a SEPARATE serial queue, so speech playback never
  // gates the appearance of later bubbles. These queues live across renders.
  const queueRef = useRef<Hop[]>([]);
  const drainingRef = useRef(false);
  const pausedRef = useRef(false);
  // Reply bubbles waiting to be spoken, played one clip at a time in arrival
  // order. Decoupled from `queueRef` so audio never blocks bubble rendering.
  const audioQueueRef = useRef<{ id: number; text: string }[]>([]);
  const audioDrainingRef = useRef(false);

  const setReplySpeaking = useCallback((id: number, speaking: boolean) => {
    setState((s) => ({
      ...s,
      bubbles: s.bubbles.map((b) =>
        b.kind === "reply" && b.id === id ? { ...b, speaking } : b,
      ),
    }));
  }, []);

  const setReplyTtsFailed = useCallback((id: number) => {
    setState((s) => ({
      ...s,
      bubbles: s.bubbles.map((b) =>
        b.kind === "reply" && b.id === id
          ? { ...b, speaking: false, ttsFailed: true }
          : b,
      ),
    }));
  }, []);

  const waitWhilePaused = useCallback(async () => {
    while (pausedRef.current) {
      await new Promise((r) => setTimeout(r, 80));
    }
  }, []);

  // Play queued reply audio one clip at a time, in arrival order, WITHOUT
  // blocking bubble rendering. The bubble is already on screen; this only
  // drives its speaking / ttsFailed state around the actual playback.
  const drainAudio = useCallback(async () => {
    if (audioDrainingRef.current) return;
    audioDrainingRef.current = true;
    try {
      while (audioQueueRef.current.length > 0) {
        await waitWhilePaused();
        // Re-read after the await: barge-in/reset can clear the queue while we
        // were paused, so the shift may now come back empty.
        const next = audioQueueRef.current.shift();
        if (!next) continue;
        const { id, text } = next;
        setReplySpeaking(id, true);
        try {
          await audio.play(text);
          setReplySpeaking(id, false);
        } catch {
          setReplyTtsFailed(id);
        }
      }
    } finally {
      audioDrainingRef.current = false;
    }
  }, [waitWhilePaused, setReplySpeaking, setReplyTtsFailed]);

  const renderHop = useCallback(
    (hop: Hop) => {
      if (hop.kind === "reply") {
        const id = newId();
        setState((s) => ({
          ...s,
          bubbles: [
            ...s.bubbles,
            {
              id,
              kind: "reply",
              text: hop.text,
              text_ids: hop.text_ids,
              speaking: false,
            },
          ],
        }));
        // Render now; speak later on the audio queue (do NOT await here, or
        // every bubble behind this reply would wait for its clip to finish).
        audioQueueRef.current.push({ id, text: hop.text });
        void drainAudio();
      } else if (hop.kind === "tool_call") {
        setState((s) => ({
          ...s,
          bubbles: [
            ...s.bubbles,
            { id: newId(), kind: "tool_call", name: hop.name, args: hop.args },
          ],
        }));
      } else {
        setState((s) => ({
          ...s,
          bubbles: [
            ...s.bubbles,
            { id: newId(), kind: "tool_result", name: hop.name, result: hop.result },
          ],
        }));
      }
    },
    [drainAudio],
  );

  const drain = useCallback(async () => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    try {
      while (queueRef.current.length > 0) {
        await waitWhilePaused();
        // Re-read after the await: a reset can clear the queue mid-pause.
        const hop = queueRef.current.shift();
        if (!hop) continue;
        renderHop(hop);
      }
    } finally {
      drainingRef.current = false;
    }
  }, [renderHop, waitWhilePaused]);

  const onHop = useCallback(
    (hop: Hop) => {
      // First hop of a turn → record time-to-first-activity (no-op outside a
      // turn, e.g. session-open/reset streams).
      latency.markFirstHop();
      // Client-side TTS prefetch is DISABLED by default: the server already warms
      // the synth when the reply is produced (AAX6_TTS_PREWARM_REPLY), so this GET
      // is redundant for cache-warming — and it fires a SECOND parallel download of
      // the same full clip at the same instant as play(), which on a constrained /
      // high-latency link steals bandwidth from play() every turn (measured: reply
      // first-audio ~1.4s). Re-enable to A/B by setting localStorage.ttsClientPrefetch="1".
      const clientPrefetch =
        typeof localStorage !== "undefined" && localStorage.getItem("ttsClientPrefetch") === "1";
      if (hop.kind === "reply" && clientPrefetch) audio.prefetch(hop.text);
      queueRef.current.push(hop);
      void drain();
    },
    [drain],
  );

  // ---- public ops ----

  const start = useCallback(async () => {
    latency.reset();
    setState((s) => ({ ...s, busy: true, streamError: null }));
    let capturedSession: { id: string; done: boolean } | null = null;
    try {
      await streamSession(
        {
          onSession: (m) => {
            capturedSession = { id: m.session_id, done: false };
            sessionIdRef.current = m.session_id;
            setState((s) => ({
              ...s,
              ready: true,
              sessionId: m.session_id,
              mode: m.mode,
              caseId: m.case_id,
              serverAgent: m.agent,
              // record what the session was BUILT with; don't clobber the user's
              // current pick (state.voiceGender) — handleStart compares the two.
              serverVoiceGender: m.voice_gender,
              customer: m.customer_data,
              bubbles: [],
              done: false,
              paused: false,
            }));
          },
          onHop: (m) => onHop(m.hop),
          onDone: (m) => {
            if (m.session_done) {
              setState((s) => ({ ...s, done: true }));
            }
          },
        },
        {
          engine: agentRef.current,
          caseId: caseIdRef.current ?? undefined,
          voiceGender: voiceGenderRef.current,
          model: modelRef.current || undefined,
        },
      );
    } catch (e: any) {
      const msg = `Failed to load session: ${e?.message ?? e}`;
      setState((s) => ({
        ...s,
        streamError: { message: msg, retry: () => void start() },
      }));
      throw e;
    } finally {
      setState((s) => ({ ...s, busy: false }));
    }
    // Avoid unused-var TS warning
    void capturedSession;
  }, [onHop]);

  // Pick a persona (pre-start only): remember the case id and re-create the
  // session for it. `onSession` repopulates customer/caseId; `started` (owned
  // by App) stays false, so the user lands back on the start screen.
  const selectCase = useCallback(
    async (caseId: string) => {
      caseIdRef.current = caseId;
      await start();
    },
    [start],
  );

  const sendUserMessage = useCallback(
    async (text: string, viaMic: boolean) => {
      if (!state.sessionId || state.done) return;
      const trimmed = text.trim();
      if (!trimmed) return;
      const sid = state.sessionId;
      setState((s) => ({
        ...s,
        bubbles: [
          ...s.bubbles,
          { id: newId(), kind: "user", text: trimmed, viaMic },
        ],
        busy: true,
        streamError: null,
      }));
      // Turn POST fired (mic transcript or typed). Promotes any pending mic
      // capture (VAD/STT stamps) into this turn; typed turns mark VAD/STT N/A.
      latency.markTurnPost(viaMic);
      try {
        await streamTurn(sid, trimmed, {
          onHop: (m) => onHop(m.hop),
          onDone: (m) => {
            latency.markDone(m.llm_ms ?? null, m.llm_hops ?? null);
            if (m.session_done) {
              setState((s) => ({ ...s, done: true }));
            }
          },
        });
      } catch (e: any) {
        const msg = `Connection lost: ${e?.message ?? e}`;
        setState((s) => ({
          ...s,
          streamError: {
            message: msg,
            retry: () => void sendUserMessage(text, viaMic),
          },
        }));
      } finally {
        setState((s) => ({ ...s, busy: false }));
      }
    },
    [state.sessionId, state.done, onHop],
  );

  // Outbound-call opening: the bot greets first (before the caller speaks).
  // Fired by App right after Start. Hops flow through the same onHop pipeline
  // (greeting bubble + TTS). No-op if the session already ended.
  const fireOpening = useCallback(async () => {
    const sid = sessionIdRef.current;  // ref: fresh id even right after start()
    if (!sid) return;
    setState((s) => ({ ...s, busy: true }));
    try {
      await streamOpening(sid, {
        onHop: (m) => onHop(m.hop),
        onDone: (m) => {
          latency.markDone(m.llm_ms ?? null, m.llm_hops ?? null);
          if (m.session_done) setState((s) => ({ ...s, done: true }));
        },
      });
    } catch {
      /* opening is best-effort; the caller can still speak first if it fails */
    } finally {
      setState((s) => ({ ...s, busy: false }));
    }
  }, [onHop]);

  const resetInFlightRef = useRef(false);

  const reset = useCallback(async () => {
    if (!state.sessionId) return;
    if (resetInFlightRef.current) return;
    resetInFlightRef.current = true;
    audio.stop();
    latency.reset();
    queueRef.current = [];
    drainingRef.current = false;
    pausedRef.current = false;
    audioQueueRef.current = [];
    audioDrainingRef.current = false;
    const sid = state.sessionId;
    setState((s) => ({
      ...s,
      bubbles: [],
      done: false,
      paused: false,
      busy: true,
      streamError: null,
    }));
    try {
      await streamReset(sid, {
        onSession: (m) => {
          sessionIdRef.current = m.session_id;
          setState((s) => ({
            ...s,
            ready: true,
            sessionId: m.session_id,
            mode: m.mode,
            caseId: m.case_id ?? s.caseId,
            serverAgent: m.agent,
            customer: m.customer_data,
          }));
        },
        onHop: (m) => onHop(m.hop),
        onDone: (m) => {
          if (m.session_done) {
            setState((s) => ({ ...s, done: true }));
          }
        },
      });
    } catch (e: any) {
      const msg = `Failed to reset: ${e?.message ?? e}`;
      setState((s) => ({
        ...s,
        streamError: { message: msg, retry: () => void reset() },
      }));
    } finally {
      setState((s) => ({ ...s, busy: false }));
      resetInFlightRef.current = false;
    }
  }, [state.sessionId, onHop]);

  const togglePause = useCallback(() => {
    const next = !pausedRef.current;
    pausedRef.current = next;
    if (next) audio.pause();
    else audio.resume();
    setState((s) => ({ ...s, paused: next }));
  }, []);

  const bargeIn = useCallback(() => {
    audio.stop();
    audioQueueRef.current = [];
    setState((s) => ({
      ...s,
      bubbles: s.bubbles.map((b) =>
        b.kind === "reply" && b.speaking ? { ...b, speaking: false } : b,
      ),
    }));
  }, []);

  const clearStreamError = useCallback(() => {
    setState((s) => ({ ...s, streamError: null }));
  }, []);

  return {
    state,
    start,
    setAgent,
    setModel,
    setVoiceGender,
    selectCase,
    fireOpening,
    sendUserMessage,
    reset,
    togglePause,
    bargeIn,
    clearStreamError,
  };
}
