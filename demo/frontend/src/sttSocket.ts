/* Chirp 3 backend STT client.
 *
 *   mic → AudioWorklet (PCM16 @ 16 kHz, ~100 ms frames) → WebSocket /api/stt
 *       → JSON events { speech_begin, stt_final, … } back
 *
 * Mirrors the source project's web/static/app.js capture path. Server-side
 * Silero VAD does endpointing and batch Chirp recognize() does transcription
 * (see demo/server/stt_ws.py). The browser Web Speech API (speech.ts) remains
 * the automatic fallback when this path is unavailable.
 */

export type ChirpEvent =
  | { type: "ready"; sample_rate: number }
  | { type: "speech_begin" }
  | { type: "stt_interim"; text: string }
  | { type: "speech_end" }
  | { type: "stt_final"; text: string }
  | { type: "turn_empty" }
  | { type: "error"; message: string; fatal?: boolean };

export type ChirpHandle = { stop: () => void };

export type ChirpCallbacks = {
  /** Server built its engines and is listening. */
  onReady?: () => void;
  /** Silero detected the caller started talking (drives barge-in). */
  onSpeechBegin?: () => void;
  /** Growing transcript while the caller is still talking (live words). */
  onInterim?: (text: string) => void;
  /** A finalized Thai transcript for one utterance. */
  onFinal?: (text: string) => void;
  /** A non-fatal error frame from the server. */
  onError?: (message: string, fatal: boolean) => void;
  /** Socket closed (caller decides whether to fall back). */
  onClose?: () => void;
};

/** Chirp STT needs a secure context with mic + AudioWorklet + WebSocket. */
export function isChirpSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof AudioWorkletNode !== "undefined" &&
    typeof window.WebSocket !== "undefined"
  );
}

function sttUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/stt`;
}

/** Open mic + worklet + WS and start streaming PCM. Resolves with a handle to
 *  stop everything. Rejects if mic / AudioWorklet setup fails (a DOMException
 *  whose `name` distinguishes permission-denied from other failures) — the
 *  caller decides whether to surface it or fall back to the browser API. */
export async function startChirp(cb: ChirpCallbacks): Promise<ChirpHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      // Echo cancellation keeps the agent's own TTS from leaking back into the
      // mic and tripping a false barge-in (matches the reference capture).
      echoCancellation: true,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });

  const AudioCtx: typeof AudioContext =
    window.AudioContext || (window as any).webkitAudioContext;
  const audioCtx = new AudioCtx();

  let ws: WebSocket | null = null;
  let workletNode: AudioWorkletNode | null = null;
  let srcNode: MediaStreamAudioSourceNode | null = null;
  let stopped = false;

  const cleanup = () => {
    stopped = true;
    try {
      ws?.close();
    } catch {
      /* noop */
    }
    try {
      if (workletNode) workletNode.port.onmessage = null;
      workletNode?.disconnect();
    } catch {
      /* noop */
    }
    try {
      srcNode?.disconnect();
    } catch {
      /* noop */
    }
    try {
      stream.getTracks().forEach((t) => t.stop());
    } catch {
      /* noop */
    }
    // close() returns a Promise; a plain try/catch won't swallow the rejection
    // (closing an already-closed ctx rejects).
    if (audioCtx.state !== "closed") audioCtx.close().catch(() => {});
  };

  try {
    await audioCtx.audioWorklet.addModule("/mic-worklet.js");
    srcNode = audioCtx.createMediaStreamSource(stream);
    workletNode = new AudioWorkletNode(audioCtx, "mic-downsampler");
    srcNode.connect(workletNode);
  } catch (e) {
    cleanup();
    throw e;
  }

  ws = new WebSocket(sttUrl());
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    // Forward each PCM frame the worklet emits.
    workletNode!.port.onmessage = (ev: MessageEvent) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(ev.data as ArrayBuffer);
    };
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") return; // this socket sends JSON only
    let msg: ChirpEvent;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    switch (msg.type) {
      case "ready":
        cb.onReady?.();
        break;
      case "speech_begin":
        cb.onSpeechBegin?.();
        break;
      case "stt_interim":
        cb.onInterim?.(msg.text);
        break;
      case "stt_final":
        cb.onFinal?.(msg.text);
        break;
      case "error":
        cb.onError?.(msg.message, !!msg.fatal);
        break;
      // speech_end / turn_empty: no client action needed.
    }
  };
  ws.onerror = () => {
    if (!stopped) cb.onError?.("speech socket error", false);
  };
  ws.onclose = () => {
    if (!stopped) cb.onClose?.();
  };

  return { stop: cleanup };
}
