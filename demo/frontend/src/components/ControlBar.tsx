import { useEffect, useState } from "react";
import { Cpu, Mic, MicOff, Pause, Play, RotateCcw, Save, Volume2, X } from "lucide-react";
import { ThinkingDot } from "./ThinkingDot";
import { LatencyMetrics } from "./LatencyMetrics";
import type { MicState } from "../hooks/useSpeechRecognition";
import type { SpeechErrorCode } from "../speech";
import { fetchModels, type Engine, type VoiceGender } from "../api";

// Preferred default checkpoint for the qwen picker (all local models live here).
const QWEN_DEFAULT = ["sft_v11", "sft_flow_v5", "sft_flow_v3", "sft_v10"];
const pickDefault = (list: string[], prefer: string[]) =>
  prefer.find((p) => list.includes(p)) ?? list[list.length - 1] ?? "";

// Pre-start, the caller picks which agent drives the call: the fine-tuned Qwen
// (sft_v2, served via vLLM) or Gemini (API). Choosing re-creates the live
// session bound to that LLM — see handleStart() in App.tsx. Once the call is
// live the choice is locked (the active model is shown in the CustomerPanel).
// Update these labels if you serve a different base model / SFT version.
const QWEN_LABEL = "Qwen3.5-9B";
const QWEN_SFT = "v2";

type Props = {
  started: boolean;
  ready: boolean;
  starting: boolean;
  startError: string;
  onStart: () => void;
  agent: Engine;
  onAgentChange: (a: Engine) => void;
  model: string;
  onModelChange: (m: string) => void;
  onBuildFlow?: () => void;
  onEditFlow?: () => void;
  voiceGender: VoiceGender;
  onVoiceGenderChange: (g: VoiceGender) => void;
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
  onSave: () => void;
  canSave: boolean;
  saving: boolean;
};

export function ControlBar({
  started,
  ready,
  starting,
  startError,
  onStart,
  agent,
  onAgentChange,
  model,
  onModelChange,
  onBuildFlow,
  onEditFlow,
  voiceGender,
  onVoiceGenderChange,
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
  onSave,
  canSave,
  saving,
}: Props) {
  const [typed, setTyped] = useState("");
  const [models, setModels] = useState<{ base: string[]; flow: string[] }>({ base: [], flow: [] });

  useEffect(() => { void fetchModels().then(setModels).catch(() => {}); }, []);

  // The list valid for the current engine, and a default pick if none is chosen yet.
  const modelList = agent === "qwen" ? models.base : [];
  useEffect(() => {
    if (started || agent === "gemini" || modelList.length === 0) return;
    if (!modelList.includes(model)) {
      onModelChange(pickDefault(modelList, QWEN_DEFAULT));
    }
  }, [agent, models, started]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!started) {
    return (
      <div className="control-bar start">
        <div
          className="agent-segmented"
          role="group"
          aria-label="Choose which agent model drives the call"
        >
          <span className="agent-segmented-label">
            <Cpu size={13} aria-hidden="true" /> Model
          </span>
          <button
            type="button"
            className={`agent-segmented-btn ${agent === "qwen" ? "on" : ""}`}
            onClick={() => onAgentChange("qwen")}
            disabled={starting}
            aria-pressed={agent === "qwen"}
            title={`Fine-tuned ${QWEN_LABEL} (SFT ${QWEN_SFT}), served locally via vLLM`}
          >
            Qwen
          </button>
          <button
            type="button"
            className={`agent-segmented-btn ${agent === "gemini" ? "on" : ""}`}
            onClick={() => onAgentChange("gemini")}
            disabled={starting}
            aria-pressed={agent === "gemini"}
            title="Gemini (cloud API) running the same pre-script playbook + tools"
          >
            Gemini
          </button>
        </div>
        {agent !== "gemini" && modelList.length > 0 && (
          <label className="agent-model" title="เลือก checkpoint ที่ vLLM เสิร์ฟ">
            <span className="agent-segmented-label">version</span>
            <select
              className="agent-model-select"
              value={modelList.includes(model) ? model : ""}
              disabled={started || starting}
              onChange={(e) => onModelChange(e.target.value)}
            >
              {modelList.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}
        <div
          className="agent-segmented"
          role="group"
          aria-label="Choose the TTS voice"
        >
          <span className="agent-segmented-label">
            <Volume2 size={13} aria-hidden="true" /> Voice
          </span>
          <button
            type="button"
            className={`agent-segmented-btn ${voiceGender === "F" ? "on" : ""}`}
            onClick={() => onVoiceGenderChange("F")}
            disabled={starting}
            aria-pressed={voiceGender === "F"}
            title="Female Chirp 3 HD voice (Despina)"
          >
            Female
          </button>
          <button
            type="button"
            className={`agent-segmented-btn ${voiceGender === "M" ? "on" : ""}`}
            onClick={() => onVoiceGenderChange("M")}
            disabled={starting}
            aria-pressed={voiceGender === "M"}
            title="Male Chirp 3 HD voice (Puck)"
          >
            Male
          </button>
        </div>
        {agent === "qwen" && onBuildFlow && (
          <button
            type="button"
            className="btn"
            onClick={onBuildFlow}
            disabled={starting}
            title="สร้างบริษัทใหม่สำหรับ flow mode"
          >
            ＋ New company
          </button>
        )}
        {agent === "qwen" && onEditFlow && (
          <button
            type="button"
            className="btn"
            onClick={onEditFlow}
            disabled={starting}
            title="แก้โครง flow (states/transitions) ของบริษัทที่เลือก"
          >
            Edit flow
          </button>
        )}
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

  const muted = micState === "muted";
  const listening = micState === "listening";
  const micLabel = !micSupported
    ? "Voice off"
    : muted
    ? "Muted"
    : listening
    ? "Listening…"
    : "Mic on";
  const micActionLabel = muted ? "Unmute microphone" : "Mute microphone";
  const micActionTitle = muted ? "Tap to unmute (Space)" : "Tap to mute (Space)";

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
      <LatencyMetrics />
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
            micSupported ? micActionLabel : "Speech not supported in this browser"
          }
          aria-pressed={muted}
          title={
            micSupported
              ? micActionTitle
              : "Web Speech API not supported — use the typed input"
          }
        >
          <span className="mic-glyph" aria-hidden="true">
            {micSupported && !muted ? <Mic size={18} /> : <MicOff size={18} />}
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
        <button
          className="btn save"
          onClick={onSave}
          disabled={!canSave || saving}
          aria-label="Save conversation"
          title={canSave ? "Save conversation" : "Nothing to save yet"}
        >
          <Save size={16} aria-hidden="true" />
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
