import { useEffect, useRef } from "react";
import type { MicState } from "./useSpeechRecognition";

export type GlobalKeyboardOpts = {
  enabled: boolean;
  mic: {
    state: MicState;
    start: () => Promise<void> | void;
    stop: () => void;
    supported: boolean;
    error: string;
    clearError: () => void;
  };
  onTogglePause: () => void;
  onRequestReset: () => void;
  onTogglePanel: () => void;
  onBargeIn: () => void;
  isTTSPlaying: () => boolean;
  done: boolean;
};

function isFormFocus(e: KeyboardEvent): boolean {
  const t = e.target;
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  return false;
}

export function useGlobalKeyboard(opts: GlobalKeyboardOpts): void {
  const optsRef = useRef(opts);
  optsRef.current = opts;

  // Tracks whether the current "listening" session was started by this hook
  // (via Space-hold) vs. by clicking the mic button. Only PTT-initiated
  // sessions are stopped by Space release.
  const pttActiveRef = useRef(false);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const o = optsRef.current;
      if (!o.enabled) return;
      // Thai IME composition path — let it through to the IME, not our handler.
      if (e.isComposing || e.keyCode === 229) return;
      if (isFormFocus(e)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.code) {
        case "Space": {
          e.preventDefault();
          if (e.repeat) return;
          if (!o.mic.supported || o.done) return;
          // Already listening from a prior click-toggle — don't take over.
          if (o.mic.state === "listening") return;
          pttActiveRef.current = true;
          void o.mic.start();
          return;
        }
        case "Escape": {
          if (o.mic.error) {
            o.mic.clearError();
          } else if (o.isTTSPlaying()) {
            o.onBargeIn();
          } else if (o.mic.state === "listening") {
            o.mic.stop();
            pttActiveRef.current = false;
          }
          return;
        }
        case "KeyR": {
          e.preventDefault();
          o.onRequestReset();
          return;
        }
        case "KeyK": {
          e.preventDefault();
          o.onTogglePanel();
          return;
        }
        case "KeyP": {
          e.preventDefault();
          o.onTogglePause();
          return;
        }
      }
    };

    const onKeyUp = (e: KeyboardEvent) => {
      const o = optsRef.current;
      if (e.code !== "Space") return;
      if (!o.enabled) {
        pttActiveRef.current = false;
        return;
      }
      if (!pttActiveRef.current) return;
      pttActiveRef.current = false;
      e.preventDefault();
      o.mic.stop();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);
}
