import { useState } from "react";
import { Cpu, Mic, MicOff, Pause, Play, RotateCcw, X } from "lucide-react";
import { ThinkingDot } from "./ThinkingDot";
import type { MicState } from "../hooks/useSpeechRecognition";
import type { SpeechErrorCode } from "../speech";
import type { Agent } from "../api";

type Props = {
  started: boolean;
  ready: boolean;
  starting: boolean;
  startError: string;
  onStart: () => void;
  agent: Agent;
  onAgentChange: (a: Agent) => void;
  micState: MicState;
  micSupported: boolean;
  micError: string;
  micErrorCode: SpeechErrorCode | "";
  onClearMicError: () => void;
  paused: boolean;
  busy: boolean;
  done: boolean;
  onToggleMic: () => void;
  onPause: () => void;
  onRequestReset: () => void;
  onTypedSubmit: (text: string) => void;
};

export function ControlBar({
  started,
  ready,
  starting,
  startError,
  onStart,
  agent,
  onAgentChange,
  micState,
  micSupported,
  micError,
  micErrorCode,
  onClearMicError,
  paused,
  busy,
  done,
  onToggleMic,
  onPause,
  onRequestReset,
  onTypedSubmit,
}: Props) {
  const [typed, setTyped] = useState("");

  if (!started) {
    return (
      <div className="control-bar start">
        <div
          className="agent-segmented"
          role="radiogroup"
          aria-label="Agent model"
        >
          <span className="agent-segmented-label" aria-hidden="true">
            <Cpu size={14} /> Agent
          </span>
          <button
            type="button"
            role="radio"
            aria-checked={agent === "qwen"}
            className={`agent-segmented-btn ${agent === "qwen" ? "on" : ""}`}
            onClick={() => onAgentChange("qwen")}
            disabled={starting}
          >
            Qwen
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={agent === "gemini"}
            className={`agent-segmented-btn ${agent === "gemini" ? "on" : ""}`}
            onClick={() => onAgentChange("gemini")}
            disabled={starting}
          >
            Gemini
          </button>
        </div>
        <button
          className="btn start"
          onClick={onStart}
          disabled={starting || !ready}
        >
          {starting ? (
            "Connecting…"
          ) : (
            <>
              <Play size={16} aria-hidden="true" /> Start
            </>
          )}
        </button>
        {startError && (
          <div className="mic-error" role="alert">
            <span>{startError}</span>
          </div>
        )}
      </div>
    );
  }

  const listening = micState === "listening";
  const micLabel = listening ? "Listening — tap to stop" : "Tap to speak";

  const focusTyped = () => {
    setTimeout(() => {
      const el = document.querySelector<HTMLInputElement>(
        ".typed-fallback input",
      );
      el?.focus();
    }, 0);
  };

  return (
    <div className="control-bar">
      {micError && micErrorCode === "permission-denied" && (
        <div className="mic-permission-recovery" role="alert">
          <div className="mic-permission-head">
            <strong>Microphone access is blocked</strong>
            <button onClick={onClearMicError} aria-label="Dismiss">
              <X size={14} aria-hidden="true" />
            </button>
          </div>
          <p className="mic-permission-body">
            Click the lock icon in your address bar and allow microphone access — then reload.
          </p>
          <details className="mic-permission-details">
            <summary>Browser instructions</summary>
            <ul>
              <li><strong>Chrome / Edge:</strong> click the lock or tune icon in the address bar → Site settings → Microphone → Allow.</li>
              <li><strong>Safari:</strong> Safari menu → Settings for This Website → Microphone → Allow.</li>
              <li><strong>Firefox:</strong> click the lock icon → Connection secure → More information → Permissions → Use the Microphone → Allow.</li>
            </ul>
          </details>
          <button
            type="button"
            className="btn mic-permission-fallback"
            onClick={() => {
              onClearMicError();
              focusTyped();
            }}
          >
            Type instead
          </button>
        </div>
      )}
      {micError && micErrorCode !== "permission-denied" && (
        <div className="mic-error" role="alert">
          <span>{micError}</span>
          <button onClick={onClearMicError} aria-label="Dismiss">
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      )}
      <div className="control-row">
        <button
          className={`btn mic ${micState}`}
          onClick={onToggleMic}
          disabled={!micSupported || done}
          aria-label={
            micSupported
              ? listening
                ? "Stop speaking"
                : "Start speaking"
              : "Speech not supported in this browser"
          }
          title={
            micSupported
              ? listening
                ? "Tap to stop"
                : "Tap to speak"
              : "Web Speech API not supported — use the typed input"
          }
        >
          <span className="mic-glyph" aria-hidden="true">
            {micSupported ? <Mic size={18} /> : <MicOff size={18} />}
          </span>
          <span className="mic-label">{micLabel}</span>
        </button>
        <button
          className={`btn pause ${paused ? "on" : ""}`}
          onClick={onPause}
          aria-label={paused ? "Resume" : "Pause"}
          title={paused ? "Resume" : "Pause"}
        >
          {paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
        </button>
        <button
          className="btn reset"
          onClick={onRequestReset}
          aria-label="Reset call"
          title="Reset call"
        >
          <RotateCcw size={16} aria-hidden="true" />
        </button>
        {busy && <ThinkingDot />}
      </div>
      <form
        className="typed-fallback"
        onSubmit={(e) => {
          e.preventDefault();
          if (typed.trim() && !done) {
            onTypedSubmit(typed.trim());
            setTyped("");
          }
        }}
      >
        <input
          type="text"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={done ? "Call ended" : "Type a message…"}
          disabled={done}
        />
        <button type="submit" disabled={!typed.trim() || done}>
          Send
        </button>
      </form>
    </div>
  );
}
