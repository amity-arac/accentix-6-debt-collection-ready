/* Browser Web Speech API wrapper, Thai locale.
 *
 * Continuous ("phone call") capture: `continuous: true` keeps the recognizer
 * open across pauses, emitting a final result each time the caller stops
 * speaking. The owning hook restarts it after the browser ends it (silence /
 * timeout) and parks it while the agent is talking. `interimResults` streams
 * the live partial transcript; `onStart` reports when capture actually begins.
 *
 * Each start() instantiates a fresh SpeechRecognition (the spec doesn't
 * support `start()` on a recognizer that's already ended).
 */

type SpeechRecognition = any;

export type SpeechErrorCode =
  | "not-supported"
  | "permission-denied"
  | "no-speech"
  | "audio-capture"
  | "network"
  | "aborted"
  | "unknown";

export type RecognizerHandle = {
  start: () => void;
  stop: () => void;
  supported: boolean;
};

export type ResultCallback = (text: string, isFinal: boolean) => void;
export type ErrorCallback = (code: SpeechErrorCode, raw?: string) => void;
export type EndCallback = () => void;

export function isSpeechSupported(): boolean {
  return !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

/** Explicitly prompt for microphone permission (so the user sees the OS dialog
 *  before we kick off SpeechRecognition, which silently fails when blocked). */
export async function requestMicPermission(): Promise<boolean> {
  if (!navigator.mediaDevices?.getUserMedia) return true; // assume allowed if unsupported
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // We don't actually use this stream; SpeechRecognition opens its own.
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

export function createRecognizer(
  onResult: ResultCallback,
  onError: ErrorCallback,
  onEnd: EndCallback,
  onStart?: EndCallback,
): RecognizerHandle {
  const SR: any =
    (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) {
    return { start: () => {}, stop: () => {}, supported: false };
  }

  let rec: SpeechRecognition | null = null;
  let running = false;

  return {
    supported: true,
    start: () => {
      if (running) return;
      rec = new SR();
      rec.lang = "th-TH";
      rec.interimResults = true;
      rec.continuous = true;
      rec.maxAlternatives = 1;

      rec.onstart = () => {
        running = true;
        onStart?.();
      };
      rec.onresult = (e: any) => {
        let interim = "";
        let final = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) final += r[0].transcript;
          else interim += r[0].transcript;
        }
        if (final) onResult(final.trim(), true);
        else if (interim) onResult(interim, false);
      };
      rec.onerror = (e: any) => {
        running = false;
        const raw = e?.error || "unknown";
        const code: SpeechErrorCode =
          raw === "not-allowed" || raw === "service-not-allowed"
            ? "permission-denied"
            : raw === "no-speech"
            ? "no-speech"
            : raw === "audio-capture"
            ? "audio-capture"
            : raw === "network"
            ? "network"
            : raw === "aborted"
            ? "aborted"
            : "unknown";
        onError(code, raw);
      };
      rec.onend = () => {
        running = false;
        onEnd();
      };

      try {
        rec.start();
        running = true;
      } catch (err) {
        running = false;
        onError("unknown", String(err));
      }
    },
    stop: () => {
      if (rec && running) {
        try {
          rec.stop();
        } catch {
          /* noop */
        }
      }
      running = false;
    },
  };
}
