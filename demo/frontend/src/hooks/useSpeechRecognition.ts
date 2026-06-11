import { useCallback, useEffect, useRef, useState } from "react";
import {
  createRecognizer,
  isSpeechSupported,
  requestMicPermission,
  type RecognizerHandle,
  type SpeechErrorCode,
} from "../speech";

export type MicState = "idle" | "listening";

const ERROR_LABEL: Record<SpeechErrorCode, string> = {
  "not-supported": "Speech recognition not supported (use Chrome or Edge)",
  "permission-denied": "Microphone permission denied — please allow it in the address bar",
  "no-speech": "No speech detected — try again",
  "audio-capture": "No microphone found",
  "network": "Speech network error",
  "aborted": "",
  "unknown": "Speech recognition error",
};

export function useSpeechRecognition(onFinal: (text: string) => void) {
  const [state, setState] = useState<MicState>("idle");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string>("");
  const [errorCode, setErrorCode] = useState<SpeechErrorCode | "">("");
  const [supported] = useState<boolean>(() => isSpeechSupported());

  // Keep latest onFinal in a ref so the recognizer doesn't churn when the
  // parent re-renders.
  const onFinalRef = useRef(onFinal);
  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  const recRef = useRef<RecognizerHandle | null>(null);
  const sentFinalRef = useRef(false);

  // Construct the recognizer once.
  useEffect(() => {
    if (!supported) return;
    const rec = createRecognizer(
      (text, isFinal) => {
        if (isFinal) {
          if (sentFinalRef.current) return;
          sentFinalRef.current = true;
          setInterim("");
          setState("idle");
          if (text) onFinalRef.current(text);
        } else {
          setInterim(text);
        }
      },
      (code, _raw) => {
        setState("idle");
        setInterim("");
        const msg = ERROR_LABEL[code] ?? "Speech recognition error";
        setError(msg);
        setErrorCode(code);
      },
      () => {
        setState("idle");
        setInterim("");
      },
    );
    recRef.current = rec;
    return () => rec.stop();
  }, [supported]);

  const start = useCallback(async () => {
    if (!supported) {
      setError(ERROR_LABEL["not-supported"]);
      setErrorCode("not-supported");
      return;
    }
    setError("");
    setErrorCode("");
    const ok = await requestMicPermission();
    if (!ok) {
      setError(ERROR_LABEL["permission-denied"]);
      setErrorCode("permission-denied");
      return;
    }
    sentFinalRef.current = false;
    setInterim("");
    setState("listening");
    recRef.current?.start();
  }, [supported]);

  const stop = useCallback(() => {
    recRef.current?.stop();
    setState("idle");
  }, []);

  const toggle = useCallback(() => {
    if (state === "listening") stop();
    else void start();
  }, [state, start, stop]);

  const clearError = useCallback(() => {
    setError("");
    setErrorCode("");
  }, []);

  return {
    state,
    interim,
    error,
    errorCode,
    supported,
    start,
    stop,
    toggle,
    clearError,
  };
}
