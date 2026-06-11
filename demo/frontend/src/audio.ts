/* Progressive TTS playback.
 *
 * Each play(text) sets a singleton <audio>'s src to the streaming GET endpoint
 * (`/api/tts?text=...`). The browser starts decoding/playing as soon as
 * enough bytes have arrived — no need to wait for the whole MP3 to download
 * before any audio is heard. Resolves when audio playback ends or fails.
 */

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
 *  runs later, the server has already synthesized (or is mid-synthesis), so
 *  the audio element gets bytes faster. Safe to call multiple times; the
 *  server dedupes concurrent requests for the same text. */
export function prefetch(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  fetch(`/api/tts?text=${encodeURIComponent(trimmed)}`, { method: "GET" })
    .then((r) => r.body && r.body.cancel().catch(() => {}))
    .catch(() => {});
}

export function play(text: string): Promise<void> {
  const trimmed = text.trim();
  if (!trimmed) return Promise.resolve();
  stop();

  const a = ensureAudio();
  a.src = `/api/tts?text=${encodeURIComponent(trimmed)}`;

  return new Promise<void>((resolve) => {
    resolveCurrent = resolve;

    const onEnded = () => {
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
      settle();
    };
    const onError = () => {
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
      settle();
    };

    a.addEventListener("ended", onEnded);
    a.addEventListener("error", onError);

    // Kick off the request immediately. The browser starts playing as soon
    // as it has enough data buffered (it doesn't wait for the full file).
    a.play().catch(() => onError());
  });
}

export function stop(): void {
  if (audio) {
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
