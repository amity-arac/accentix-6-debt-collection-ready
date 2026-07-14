/* Progressive TTS playback.
 *
 * Each play(text) sets a singleton <audio>'s src to the streaming GET endpoint
 * (`/api/tts?text=...`). The browser starts decoding/playing as soon as
 * enough bytes have arrived — no need to wait for the whole Ogg/Opus clip to
 * download before any audio is heard. Resolves when audio playback ends or fails.
 */

import * as latency from "./latency";

let audio: HTMLAudioElement | null = null;
let resolveCurrent: (() => void) | null = null;

function ensureAudio(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio();
    audio.preload = "auto";
    // Hint to the browser that it's safe to start playing as soon as enough
    // audio is buffered — don't wait for canplaythrough.
    (audio as any).autoplay = false;
  }
  return audio;
}

function settle() {
  if (resolveCurrent) {
    const r = resolveCurrent;
    resolveCurrent = null;
    r();
  }
}

/** Fire-and-forget warm-up of the server cache for `text`. When play(text)
 *  runs later, the server has already synthesized the clip, so the audio
 *  element gets bytes immediately. Safe to call multiple times; the server
 *  dedupes concurrent requests for the same text via a per-text lock.
 *
 *  We DRAIN the response body to completion rather than cancelling it: the
 *  server only writes `_CACHE[text]` after its stream fully drains
 *  (demo/server/tts.py), and it holds the per-text lock until then. Cancelling
 *  (the old `r.body.cancel()`) aborted the server generator before it cached,
 *  so the later play() re-synthesized from scratch. Draining lets the cache
 *  populate and makes play() a warm-cache hit. */
export function prefetch(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  fetch(`/api/tts?text=${encodeURIComponent(trimmed)}`, { method: "GET" })
    .then(async (r) => {
      const body = r.body;
      if (!body) return;
      const reader = body.getReader();
      // Read to end and discard — this is what lets the server finish the
      // stream and cache the bytes for the subsequent play().
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done } = await reader.read();
        if (done) break;
      }
    })
    .catch(() => {});
}

// TEMPORARY DIAGNOSTIC — logs when the browser fires each media-readiness event
// vs. when the audio request started, so we can see whether <audio> starts
// playing on the first chunk or waits for (most of) the clip to download.
// Toggle off by setting localStorage.ttsDebug = "0". Remove once diagnosed.
const TTS_DEBUG =
  typeof localStorage === "undefined" || localStorage.getItem("ttsDebug") !== "0";

// Cleanup for the previous turn's diagnostic listeners — invoked before attaching
// new ones so the singleton <audio> never accumulates duplicate loggers.
let diagDetach: (() => void) | null = null;

function attachTimingDiag(a: HTMLAudioElement, t0: number): () => void {
  diagDetach?.();
  diagDetach = null;
  if (!TTS_DEBUG) return () => {};
  const bufEnd = () => {
    try {
      return a.buffered.length ? a.buffered.end(a.buffered.length - 1).toFixed(2) : "-";
    } catch {
      return "-";
    }
  };
  const names = [
    "loadstart", "loadedmetadata", "loadeddata", "progress", "canplay",
    "canplaythrough", "playing", "waiting", "stalled", "suspend", "ended", "error",
  ];
  const log = (e: Event) => {
    const dt = (performance.now() - t0).toFixed(0);
    // eslint-disable-next-line no-console
    console.log(
      `[tts-timing] +${dt}ms  ${e.type.padEnd(15)} readyState=${a.readyState} bufferedEnd=${bufEnd()}s`,
    );
  };
  names.forEach((n) => a.addEventListener(n, log));
  const detach = () => names.forEach((n) => a.removeEventListener(n, log));
  diagDetach = detach;
  return detach;
}

export function play(text: string): Promise<void> {
  const trimmed = text.trim();
  if (!trimmed) return Promise.resolve();
  stop();

  const a = ensureAudio();
  const t0 = performance.now();
  const detachDiag = attachTimingDiag(a, t0);
  a.src = `/api/tts?text=${encodeURIComponent(trimmed)}`;
  // TTS request fired — the start of the time-to-first-audio measurement (only
  // the first clip of a turn counts; the store guards subsequent calls).
  latency.markTtsRequest();

  return new Promise<void>((resolve) => {
    resolveCurrent = resolve;

    // First audible sample: the end of the TTS latency measurement.
    const onPlaying = () => {
      latency.markTtsPlaying();
    };
    const onEnded = () => {
      a.removeEventListener("playing", onPlaying);
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
      detachDiag();
      settle();
    };
    const onError = () => {
      a.removeEventListener("playing", onPlaying);
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
      detachDiag();
      settle();
    };

    a.addEventListener("playing", onPlaying);
    a.addEventListener("ended", onEnded);
    a.addEventListener("error", onError);

    // Kick off the request immediately. The browser starts playing as soon
    // as it has enough data buffered (it doesn't wait for the full file).
    a.play().catch(() => onError());
  });
}

export function stop(): void {
  // Only tear the element down if it actually has a source loaded. play() calls
  // stop() first every time; calling load() on an already-idle element (e.g. a
  // play() right after a prior stop/barge-in/reset) forces a needless
  // media-pipeline re-init (~10-40ms) for no reason.
  if (audio && (audio.src || audio.currentSrc)) {
    try {
      audio.pause();
    } catch {
      /* noop */
    }
    audio.removeAttribute("src");
    audio.load();
  }
  settle();
}

export function pause(): void {
  if (audio && !audio.paused) {
    audio.pause();
  }
}

export function resume(): void {
  if (audio && audio.paused && audio.src) {
    audio.play().catch(() => {
      /* noop */
    });
  }
}

export function isPlaying(): boolean {
  return !!audio && !audio.paused && audio.currentTime > 0 && !audio.ended;
}
