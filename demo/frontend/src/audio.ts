/* Progressive TTS playback via the Web Audio API.
 *
 * The server (`/api/tts`) streams raw PCM — headerless little-endian signed
 * 16-bit @ 24 kHz (Chirp 3 HD `audio_encoding=PCM`). We deliberately do NOT use
 * an `<audio>` element: pointing `<audio>.src` at a streamed container (OGG/Opus)
 * makes the browser demux + init a codec + fill an opaque readiness watermark
 * before it starts, which cost ~1.4s to first audio even though bytes arrived in
 * ~5ms. PCM has no container and no decode step, so we schedule each chunk on an
 * `AudioContext` the instant it arrives and the first samples are audible almost
 * immediately.
 *
 * Each `play(text)` fetches the stream, converts every chunk Int16 -> Float32,
 * wraps it in an `AudioBuffer`, and starts an `AudioBufferSourceNode` at a running
 * `nextStartTime` cursor on the AudioContext clock (sample-accurate, gapless).
 * Resolves when the stream has ended AND the last scheduled source has finished
 * playing. The public interface (play/stop/pause/resume/isPlaying/prefetch) is
 * unchanged from the previous `<audio>` implementation, so callers are untouched.
 */

import * as latency from "./latency";

// Chirp 3 HD streams PCM at this rate (matches DEFAULT_SAMPLE_RATE server-side).
const SAMPLE_RATE = 24000;
// Small scheduler lead: schedule the first sample slightly ahead of the clock so
// a late-arriving chunk doesn't underrun into a click. ~80ms is inaudible as
// startup delay but comfortably covers network jitter between chunks.
const LEAD = 0.08;
// One-shot gesture events used to unlock (resume) a suspended AudioContext. An
// AudioContext created before any user interaction starts `suspended`; browsers
// require a user gesture to resume it. We attach these once and remove them on
// the first gesture.
const UNLOCK_EVENTS = ["pointerdown", "keydown", "touchstart"] as const;

let ctx: AudioContext | null = null;
let gainNode: GainNode | null = null;

// Per-play state. `playToken` increments on every play()/stop() so a superseded
// play()'s async work (in-flight fetch reads, source.onended callbacks) can bail
// instead of mutating state for the new play.
let playToken = 0;
let currentAbort: AbortController | null = null;
let resolveCurrent: (() => void) | null = null;
const liveSources = new Set<AudioBufferSourceNode>();
let nextStartTime = 0;

function makeContext(): AudioContext {
  const AC: typeof AudioContext =
    window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  try {
    // Native 24 kHz context: PCM buffers play at their native rate and the whole
    // continuous output is resampled to the device rate once, which keeps chunk
    // boundaries gapless. (`latencyHint: interactive` biases toward low output latency.)
    return new AC({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
  } catch {
    // Some browsers reject a forced sampleRate. Fall back to the device rate;
    // createBuffer() below still declares SAMPLE_RATE, so pitch stays correct
    // (the graph resamples each buffer) — we only lose the native-rate guarantee.
    return new AC({ latencyHint: "interactive" });
  }
}

function attachUnlock(context: AudioContext): void {
  const unlock = () => {
    void context.resume().catch(() => {});
    for (const ev of UNLOCK_EVENTS) window.removeEventListener(ev, unlock);
  };
  for (const ev of UNLOCK_EVENTS) window.addEventListener(ev, unlock, { passive: true });
}

function ensureContext(): AudioContext {
  if (!ctx) {
    ctx = makeContext();
    gainNode = ctx.createGain();
    gainNode.connect(ctx.destination);
    attachUnlock(ctx);
  }
  return ctx;
}

function settle(): void {
  if (resolveCurrent) {
    const r = resolveCurrent;
    resolveCurrent = null;
    r();
  }
}

/** Fire-and-forget warm-up of the server cache for `text`. When play(text) runs
 *  later, the server has already synthesized the clip, so it emits instantly.
 *
 *  We DRAIN the response body to completion (rather than cancelling): the server
 *  only writes `_CACHE[text]` after its stream fully drains and it holds the
 *  fan-out producer open until then. Draining lets the cache populate so the
 *  later play() is a warm-cache hit. */
export function prefetch(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  fetch(`/api/tts?text=${encodeURIComponent(trimmed)}`, { method: "GET" })
    .then(async (r) => {
      const body = r.body;
      if (!body) return;
      const reader = body.getReader();
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done } = await reader.read();
        if (done) break;
      }
    })
    .catch(() => {});
}

export function play(text: string): Promise<void> {
  const trimmed = text.trim();
  if (!trimmed) return Promise.resolve();
  stop(); // cancel/settle anything currently playing

  const context = ensureContext();
  void context.resume().catch(() => {}); // best-effort unlock (also via gesture listeners)

  const token = ++playToken;
  const abort = new AbortController();
  currentAbort = abort;
  liveSources.clear();
  nextStartTime = 0;

  // TTS request fired — start of the time-to-first-audio measurement (only the
  // first clip of a turn counts; the latency store guards subsequent calls).
  latency.markTtsRequest();

  return new Promise<void>((resolve) => {
    resolveCurrent = resolve;

    let streamEnded = false;
    let firstScheduled = false;
    let leftover: Uint8Array | null = null;

    // Resolve once the stream is fully read AND every scheduled buffer has ended.
    const finishIfDone = () => {
      if (token !== playToken) return; // superseded by a newer play()/stop()
      if (streamEnded && liveSources.size === 0) settle();
    };

    const scheduleChunk = (usableBytes: Uint8Array) => {
      const nSamples = usableBytes.byteLength >> 1;
      if (nSamples === 0) return;
      const view = new DataView(
        usableBytes.buffer,
        usableBytes.byteOffset,
        nSamples * 2,
      );
      const buf = context.createBuffer(1, nSamples, SAMPLE_RATE);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < nSamples; i++) {
        const s = view.getInt16(i * 2, true); // little-endian
        ch[i] = s < 0 ? s / 0x8000 : s / 0x7fff;
      }
      const src = context.createBufferSource();
      src.buffer = buf;
      src.connect(gainNode!);
      const startAt = Math.max(context.currentTime + LEAD, nextStartTime);
      src.start(startAt);
      nextStartTime = startAt + buf.duration;
      liveSources.add(src);
      // First audible buffer of the turn: end of the TTS latency measurement.
      if (!firstScheduled) {
        firstScheduled = true;
        latency.markTtsPlaying();
      }
      src.onended = () => {
        liveSources.delete(src);
        finishIfDone();
      };
    };

    void (async () => {
      try {
        const res = await fetch(`/api/tts?text=${encodeURIComponent(trimmed)}`, {
          signal: abort.signal,
        });
        // Attribute TTS latency to a warm-cache hit vs a cold synth. The server
        // tags the response `Server-Timing: cache;desc="hit|miss"` (app.py); the
        // benchmark reads this to verify a cold run and report hit-rate.
        const st = res.headers.get("Server-Timing");
        if (st) {
          const m = /cache;desc="?(hit|miss)"?/.exec(st);
          if (m) latency.markTtsCache(m[1] as "hit" | "miss");
        }
        const body = res.body;
        if (!res.ok || !body) {
          streamEnded = true;
          finishIfDone();
          return;
        }
        const reader = body.getReader();
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (token !== playToken) return; // barge-in/reset superseded us
          if (!value || value.length === 0) continue;

          // Merge any odd byte carried over from the previous chunk, then keep a
          // new odd trailing byte for the next chunk — Int16 samples are 2 bytes
          // and a network chunk can split one across a boundary.
          let bytes = value;
          if (leftover) {
            const merged = new Uint8Array(leftover.length + value.length);
            merged.set(leftover);
            merged.set(value, leftover.length);
            bytes = merged;
            leftover = null;
          }
          if (bytes.byteLength % 2 !== 0) {
            const cut = bytes.byteLength - 1;
            leftover = bytes.subarray(cut); // 1 byte, held for next chunk
            bytes = bytes.subarray(0, cut);
          }
          if (bytes.byteLength > 0) scheduleChunk(bytes);
        }
        streamEnded = true;
        finishIfDone();
      } catch {
        // Aborted (barge-in) or network/synth failure. Mirror the old <audio>
        // error path: settle (resolve) rather than reject — the caller treats a
        // resolved play() as "done", and a superseded token is handled by stop().
        if (token !== playToken) return;
        streamEnded = true;
        finishIfDone();
      }
    })();
  });
}

export function stop(): void {
  // Supersede any in-flight play() async work (fetch reads, onended callbacks).
  playToken++;
  if (currentAbort) {
    try {
      currentAbort.abort();
    } catch {
      /* noop */
    }
    currentAbort = null;
  }
  for (const src of liveSources) {
    try {
      src.onended = null;
      src.stop();
    } catch {
      /* already stopped */
    }
    try {
      src.disconnect();
    } catch {
      /* noop */
    }
  }
  liveSources.clear();
  nextStartTime = 0;
  settle();
}

export function pause(): void {
  if (ctx && ctx.state === "running") void ctx.suspend().catch(() => {});
}

export function resume(): void {
  if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => {});
}

export function isPlaying(): boolean {
  return !!ctx && ctx.state === "running" && liveSources.size > 0;
}
