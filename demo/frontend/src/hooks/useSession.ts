import { useCallback, useRef, useState } from "react";
import {
  streamSession,
  streamTurn,
  streamReset,
  type Agent,
  type CustomerData,
  type Hop,
} from "../api";
import * as audio from "../audio";

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
  agent: Agent;
  serverAgent: Agent | null;
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
    customer: {},
    bubbles: [],
    busy: false,
    done: false,
    paused: false,
    streamError: null,
  });
  // Latest user-chosen agent, read inside start() to avoid closure staleness.
  const agentRef = useRef<Agent>("qwen");

  const setAgent = useCallback((agent: Agent) => {
    agentRef.current = agent;
    setState((s) => ({ ...s, agent }));
  }, []);

  // Hops arrive over the network at their own pace; we still serialize the
  // rendering so a `reply` hop's TTS playback finishes before the next bubble
  // appears. This queue lives across renders.
  const queueRef = useRef<Hop[]>([]);
  const drainingRef = useRef(false);
  const pausedRef = useRef(false);

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

  const renderHop = useCallback(
    async (hop: Hop) => {
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
              speaking: true,
            },
          ],
        }));
        try {
          await audio.play(hop.text);
          setReplySpeaking(id, false);
        } catch {
          setReplyTtsFailed(id);
        }
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
    [setReplySpeaking, setReplyTtsFailed],
  );

  const drain = useCallback(async () => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    try {
      while (queueRef.current.length > 0) {
        await waitWhilePaused();
        const hop = queueRef.current.shift()!;
        await renderHop(hop);
      }
    } finally {
      drainingRef.current = false;
    }
  }, [renderHop, waitWhilePaused]);

  const onHop = useCallback(
    (hop: Hop) => {
      // Start warming the TTS cache the moment a reply hop arrives, so the
      // Chirp 3 HD round-trip overlaps with the animation queue draining any
      // tool bubbles still queued ahead of this reply.
      if (hop.kind === "reply") audio.prefetch(hop.text);
      queueRef.current.push(hop);
      void drain();
    },
    [drain],
  );

  // ---- public ops ----

  const start = useCallback(async () => {
    setState((s) => ({ ...s, busy: true, streamError: null }));
    let capturedSession: { id: string; done: boolean } | null = null;
    try {
      await streamSession(
        {
          onSession: (m) => {
            capturedSession = { id: m.session_id, done: false };
            setState((s) => ({
              ...s,
              ready: true,
              sessionId: m.session_id,
              mode: m.mode,
              caseId: m.case_id,
              serverAgent: m.agent,
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
        { agent: agentRef.current },
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
      try {
        await streamTurn(sid, trimmed, {
          onHop: (m) => onHop(m.hop),
          onDone: (m) => {
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

  const resetInFlightRef = useRef(false);

  const reset = useCallback(async () => {
    if (!state.sessionId) return;
    if (resetInFlightRef.current) return;
    resetInFlightRef.current = true;
    audio.stop();
    queueRef.current = [];
    drainingRef.current = false;
    pausedRef.current = false;
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
    sendUserMessage,
    reset,
    togglePause,
    bargeIn,
    clearStreamError,
  };
}
