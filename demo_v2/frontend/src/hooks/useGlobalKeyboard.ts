import { useEffect, useRef } from "react";

export type GlobalKeyboardOpts = {
  enabled: boolean;
  mic: {
    muted: boolean;
    toggleMute: () => void;
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
          // Phone-call model: tap Space to mute / unmute the live mic.
          o.mic.toggleMute();
          return;
        }
        case "Escape": {
          if (o.mic.error) {
            o.mic.clearError();
          } else if (o.isTTSPlaying()) {
            // Barge in: cut the agent's TTS. The mic gate reopens as soon as
            // playback stops, so the caller can speak immediately.
            o.onBargeIn();
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

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);
}
