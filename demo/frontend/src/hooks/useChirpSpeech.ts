import { useCallback, useEffect, useRef, useState } from "react";
import {
  isChirpSupported,
  startChirp,
  type ChirpHandle,
} from "../sttSocket";
import type { SpeechErrorCode } from "../speech";

// Chirp 3 backend STT, exposed with the SAME shape as useSpeechRecognition so
// App.tsx can use either interchangeably (see the engine switch in App.tsx).
//
// Phone-call mic model, identical contract to the browser recognizer:
//   - onSpeechStart fires when Silero detects the caller started talking
//     (server `speech_begin`) — the parent uses it to barge in on the agent's
//     TTS.
//   - onFinal delivers each finalized utterance transcript to send as a turn.
//
// Chirp gives no live interim transcript (Thai rarely yields useful partials),
// so `interim` stays empty — the field exists only for interface parity.
//
// If the backend STT can't run (no torch / no GCP creds → fatal error frame),
// or the socket fails before it's ready, we call `onUnavailable` so the parent
// can fall back to the browser Web Speech API. Microphone permission / capture
// failures are NOT a fallback trigger — the browser recognizer would hit the
// same wall — so those surface as a recovery error instead.

export type ChirpSpeechOpts = {
  enabled: boolean;
  onFinal: (text: string) => void;
  onSpeechStart?: () => void;
  /** Backend STT path is unavailable — switch to the browser recognizer. */
  onUnavailable?: (reason?: string) => void;
};

export function useChirpSpeech({
  enabled,
  onFinal,
  onSpeechStart,
  onUnavailable,
}: ChirpSpeechOpts) {
  const [muted, setMuted] = useState(false);
  const [listening, setListening] = useState(false);
  // Live, growing transcript while the caller talks (incremental recognize on
  // the server). Cleared when the utterance finalizes.
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string>("");
  const [errorCode, setErrorCode] = useState<SpeechErrorCode | "">("");
  const [supported] = useState<boolean>(() => isChirpSupported());

  // Keep latest callbacks in refs so the socket effect doesn't churn on every
  // parent re-render.
  const onFinalRef = useRef(onFinal);
  const onSpeechStartRef = useRef(onSpeechStart);
  const onUnavailableRef = useRef(onUnavailable);
  useEffect(() => {
    onFinalRef.current = onFinal;
    onSpeechStartRef.current = onSpeechStart;
    onUnavailableRef.current = onUnavailable;
  }, [onFinal, onSpeechStart, onUnavailable]);

  const handleRef = useRef<ChirpHandle | null>(null);
  const startingRef = useRef(false);
  const readyRef = useRef(false);
  const fellBackRef = useRef(false);

  const stop = useCallback(() => {
    readyRef.current = false;
    setListening(false);
    setInterim("");
    const h = handleRef.current;
    handleRef.current = null;
    h?.stop();
  }, []);

  const fallback = useCallback(
    (reason?: string) => {
      if (fellBackRef.current) return; // fall back at most once
      fellBackRef.current = true;
      stop();
      onUnavailableRef.current?.(reason);
    },
    [stop],
  );

  // Reconcile the socket with the desired capture state (call live AND not
  // muted). Opening the mic + worklet + WS is async; guards prevent double
  // starts and stop a late-resolving handle if we were torn down meanwhile.
  useEffect(() => {
    const shouldRun = supported && enabled && !muted && !fellBackRef.current;
    if (!shouldRun) {
      stop();
      return;
    }
    if (handleRef.current || startingRef.current) return;

    startingRef.current = true;
    let cancelled = false;
    startChirp({
      onReady: () => {
        readyRef.current = true;
        setListening(true);
      },
      onSpeechBegin: () => {
        setInterim(""); // fresh utterance — clear any leftover interim
        onSpeechStartRef.current?.();
      },
      onInterim: (text) => setInterim(text),
      onFinal: (text) => {
        setInterim(""); // committed — the final bubble takes over
        const trimmed = text.trim();
        if (trimmed) onFinalRef.current(trimmed);
      },
      onError: (message, fatal) => {
        // Fatal (engines unavailable) or an error before we ever went ready →
        // the backend path won't work; fall back to the browser recognizer.
        if (fatal || !readyRef.current) {
          fallback(message);
        } else {
          setError(message);
          setErrorCode("unknown");
        }
      },
      onClose: () => {
        setListening(false);
        // A close before/around ready, or any unexpected drop while we still
        // want to run, means the path is broken — fall back.
        fallback("speech connection closed");
      },
    })
      .then((h) => {
        startingRef.current = false;
        if (cancelled) {
          h.stop();
          return;
        }
        handleRef.current = h;
      })
      .catch((e: any) => {
        startingRef.current = false;
        const name = e?.name || "";
        if (name === "NotAllowedError" || name === "SecurityError") {
          // Mic blocked — the browser recognizer would hit the same wall.
          // Surface the recovery UI and park the line (mute).
          setMuted(true);
          setError(
            "Microphone permission denied — please allow it in the address bar",
          );
          setErrorCode("permission-denied");
        } else if (name === "NotFoundError") {
          setMuted(true);
          setError("No microphone found");
          setErrorCode("audio-capture");
        } else {
          // AudioWorklet / other setup failure — try the browser recognizer.
          fallback(String(e?.message ?? e));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [supported, enabled, muted, stop, fallback]);

  // Tear down on unmount.
  useEffect(() => () => stop(), [stop]);

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
