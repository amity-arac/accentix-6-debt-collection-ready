import { useCallback, useEffect, useRef, useState } from "react";
import {
  createRecognizer,
  isSpeechSupported,
  type RecognizerHandle,
  type SpeechErrorCode,
} from "../speech";

// Phone-call mic model. The recognizer runs continuously for the whole call
// (while `enabled` and not muted) — INCLUDING while the agent is speaking, so
// the caller can talk over it (barge-in). The owning component decides what to
// do with what it hears:
//   - onSpeechStart fires the instant the caller starts talking (first real
//     interim). The parent uses it to cut the agent's TTS mid-sentence.
//   - onFinal delivers each finished utterance to be sent as a turn.
//
// Three observable states are derived by the parent (muted / listening /
// waiting); this hook just owns `muted` (the mute button) and reports activity.
//
// Echo note: a hot mic during TTS can hear the agent's own voice. Chrome's
// SpeechRecognition applies echo cancellation to its capture by default, and
// the interim length gate below ignores single-character blips, so barge-in is
// reliable on a headset and usually fine on a laptop. Speakers in a loud room
// are the worst case.
export type MicState = "muted" | "listening" | "waiting";

const ERROR_LABEL: Record<SpeechErrorCode, string> = {
  "not-supported": "Speech recognition not supported (use Chrome or Edge)",
  "permission-denied": "Microphone permission denied — please allow it in the address bar",
  "no-speech": "", // benign in continuous mode — we just restart and keep listening
  "audio-capture": "No microphone found",
  "network": "Speech network error",
  "aborted": "",
  "unknown": "Speech recognition error",
};

// Browser rejects start() called in the same tick the previous session ended,
// so defer the continuous restart by a beat.
const RESTART_DELAY_MS = 250;
// Ignore interim transcripts shorter than this before declaring "the caller is
// talking" — filters single-character noise / residual TTS echo from tripping
// a false barge-in.
const BARGE_IN_MIN_CHARS = 2;

export type SpeechRecognitionOpts = {
  // The call is live (between Start and end-of-call). The recognizer runs
  // whenever this is true and the caller hasn't muted.
  enabled: boolean;
  onFinal: (text: string) => void;
  // Fired once per utterance, the moment the caller starts speaking. Used to
  // barge in on (interrupt) the agent's TTS.
  onSpeechStart?: () => void;
};

export function useSpeechRecognition({
  enabled,
  onFinal,
  onSpeechStart,
}: SpeechRecognitionOpts) {
  const [muted, setMuted] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string>("");
  const [errorCode, setErrorCode] = useState<SpeechErrorCode | "">("");
  const [supported] = useState<boolean>(() => isSpeechSupported());

  // Keep latest callbacks in refs so the recognizer doesn't churn when the
  // parent re-renders.
  const onFinalRef = useRef(onFinal);
  const onSpeechStartRef = useRef(onSpeechStart);
  useEffect(() => {
    onFinalRef.current = onFinal;
    onSpeechStartRef.current = onSpeechStart;
  }, [onFinal, onSpeechStart]);

  const recRef = useRef<RecognizerHandle | null>(null);
  // Latest desired "should be capturing" intent (call live AND not muted).
  // Read inside the recognizer's onend to decide whether to auto-restart.
  const shouldRunRef = useRef(false);
  const mutedRef = useRef(false);
  // Per-utterance latch: have we already fired onSpeechStart for the phrase
  // currently being spoken? Reset on each final so the next phrase re-arms it.
  const spokeRef = useRef(false);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Construct the recognizer once.
  useEffect(() => {
    if (!supported) return;
    const rec = createRecognizer(
      (text, isFinal) => {
        if (isFinal) {
          spokeRef.current = false;
          const trimmed = text.trim();
          setInterim("");
          if (trimmed) onFinalRef.current(trimmed);
        } else {
          // First substantive interim of this phrase → the caller is talking.
          if (!spokeRef.current && text.trim().length >= BARGE_IN_MIN_CHARS) {
            spokeRef.current = true;
            onSpeechStartRef.current?.();
          }
          setInterim(text);
        }
      },
      (code, _raw) => {
        setInterim("");
        spokeRef.current = false;
        // A blocked / missing mic is terminal: park the line (mute) and surface
        // the recovery UI rather than restart-looping into the same failure.
        if (code === "permission-denied" || code === "audio-capture") {
          shouldRunRef.current = false;
          mutedRef.current = true;
          setMuted(true);
          setListening(false);
          setError(ERROR_LABEL[code]);
          setErrorCode(code);
          return;
        }
        // Soft errors (no-speech / aborted / network / unknown): onend will
        // restart us if we still should be running. Only surface the loud ones.
        if (code === "network" || code === "unknown") {
          setError(ERROR_LABEL[code]);
          setErrorCode(code);
        }
        setListening(false);
      },
      () => {
        // onEnd — continuous capture ended (silence, timeout, or our stop()).
        setListening(false);
        setInterim("");
        spokeRef.current = false;
        if (shouldRunRef.current) {
          if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
          restartTimerRef.current = setTimeout(() => {
            if (!shouldRunRef.current) return;
            recRef.current?.start();
          }, RESTART_DELAY_MS);
        }
      },
      () => {
        // onStart — capture is actually live.
        setListening(true);
      },
    );
    recRef.current = rec;
    return () => {
      if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
      rec.stop();
    };
  }, [supported]);

  // Reconcile the recognizer with the desired capture state. Runs whenever the
  // call goes live/ends (enabled) or the caller mutes/unmutes.
  useEffect(() => {
    mutedRef.current = muted;
    const shouldRun = supported && enabled && !muted;
    shouldRunRef.current = shouldRun;
    if (shouldRun) {
      recRef.current?.start(); // no-op if already capturing
    } else {
      if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
      recRef.current?.stop();
      setInterim("");
    }
  }, [supported, enabled, muted]);

  const toggleMute = useCallback(() => {
    setError("");
    setErrorCode("");
    setMuted((m) => !m);
  }, []);

  const clearError = useCallback(() => {
    setError("");
    setErrorCode("");
  }, []);

  return {
    muted,
    listening,
    interim,
    error,
    errorCode,
    supported,
    toggleMute,
    setMuted,
    clearError,
  };
}
