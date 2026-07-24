import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { CustomerPanel } from "./components/CustomerPanel";
import { ChatStream } from "./components/ChatStream";
import { ControlBar } from "./components/ControlBar";
import { ResetConfirmModal } from "./components/ResetConfirmModal";
import { PersonaPickerModal } from "./components/PersonaPickerModal";
import { FlowBuilderModal } from "./components/FlowBuilderModal";
import { SaveDialog } from "./components/SaveDialog";
import { EndOfCallCard } from "./components/EndOfCallCard";
import { ShortcutsHint } from "./components/ShortcutsHint";
import { useSession } from "./hooks/useSession";
import { useSpeechRecognition, type MicState } from "./hooks/useSpeechRecognition";
import { useChirpSpeech } from "./hooks/useChirpSpeech";
import { useGlobalKeyboard } from "./hooks/useGlobalKeyboard";
import { requestMicPermission } from "./speech";
import { isChirpSupported } from "./sttSocket";
import {
  fetchCases,
  fetchFlowCompanies,
  saveTrajectory,
  type Engine,
  type PersonaCase,
} from "./api";
import * as audio from "./audio";

type SaveState = { phase: "idle" | "saving" | "saved" | "error"; message: string };

// Fallback flow-supported companies until /api/flow/companies responds.
const FLOW_COMPANIES_DEFAULT = ["AEON", "JAI", "KS", "AIS"];

export default function App() {
  const {
    state,
    start,
    setAgent,
    setVoiceGender,
    selectCase,
    fireOpening,
    sendUserMessage,
    reset,
    togglePause,
    bargeIn,
    clearStreamError,
  } = useSession();
  const [started, setStarted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string>("");
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [resetModalOpen, setResetModalOpen] = useState(false);
  const [personaModalOpen, setPersonaModalOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [cases, setCases] = useState<PersonaCase[]>([]);
  const [flowCompanies, setFlowCompanies] = useState<string[]>(FLOW_COMPANIES_DEFAULT);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ phase: "idle", message: "" });
  const initRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const initSession = useCallback(async (): Promise<boolean> => {
    setStarting(true);
    setStartError("");
    try {
      await start();
      return true;
    } catch (e: any) {
      setStartError(String(e?.message ?? e));
      return false;
    } finally {
      setStarting(false);
    }
  }, [start]);

  // Auto-create the session on mount so the CustomerPanel populates
  // immediately. No agent reply fires until the user clicks Start and
  // sends their first message — see `_stream_session_only` on the server.
  useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;
    void initSession();
  }, [initSession]);

  // Benchmark hook (?bench=1 only): expose the session reset so the E2E harness
  // (benchmark/e2e) can start a fresh conversation every N turns without the
  // confirm modal. Resetting keeps each measured turn at a realistic call depth
  // (a long single session drifts out-of-distribution). No-op in normal use.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!/[?&]bench=1(?:&|$)/.test(window.location.search)) return;
    (window as unknown as { __aax6Reset?: () => Promise<void> }).__aax6Reset = reset;
  }, [reset]);

  // Load the persona catalog once for the picker. Non-fatal: if it fails the
  // picker just shows an empty state and the default session still works.
  useEffect(() => {
    let cancelled = false;
    void fetchCases()
      .then((rows) => {
        if (!cancelled) setCases(rows);
      })
      .catch(() => {
        /* picker degrades to empty; default session unaffected */
      });
    void fetchFlowCompanies()
      .then((cos) => {
        if (!cancelled && cos.length) setFlowCompanies(cos);
      })
      .catch(() => {
        /* keep the default flow-supported set */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelectPersona = useCallback(
    async (caseId: string) => {
      setPersonaModalOpen(false);
      setStarting(true);
      setStartError("");
      try {
        await selectCase(caseId);
      } catch (e: any) {
        setStartError(String(e?.message ?? e));
      } finally {
        setStarting(false);
      }
    },
    [selectCase],
  );

  // Flow mode ships a FlowSpec per company (each carries the company's own
  // catalog). Companies without one fall back to AEON, so handle the engine
  // toggle explicitly: entering Flow snaps to an AEON persona ONLY if the
  // current persona's company isn't flow-supported (so the switch is visible,
  // not a silent server override at Start); leaving Flow restores the prior one.
  const flowSupported = useCallback(
    (caseId: string | null) => {
      const parts = caseId?.split("-");
      return !!parts && parts.length > 1 && flowCompanies.includes(parts[1]);
    },
    [flowCompanies],
  );
  const preFlowCaseIdRef = useRef<string | null>(null);
  const handleEngineChange = useCallback(
    (e: Engine) => {
      const prev = state.agent;
      setAgent(e);
      if (e === "flow" && prev !== "flow") {
        if (state.caseId && !flowSupported(state.caseId)) {
          preFlowCaseIdRef.current = state.caseId;
          const aeon = cases.find((c) => c.company === "AEON");
          if (aeon) void handleSelectPersona(aeon.id);
        }
      } else if (e !== "flow" && prev === "flow") {
        const restore = preFlowCaseIdRef.current;
        preFlowCaseIdRef.current = null;
        if (restore) void handleSelectPersona(restore);
      }
    },
    [state.agent, state.caseId, cases, setAgent, handleSelectPersona, flowSupported],
  );

  // While Flow is active the picker only offers flow-supported companies —
  // prevents re-introducing the silent fallback by picking an unsupported one.
  const pickerCases =
    state.agent === "flow"
      ? cases.filter((c) => flowCompanies.includes(c.company))
      : cases;

  // After the Builder creates a company: refresh companies + personas, switch to
  // Flow, and jump into the new demo persona so it's immediately playable.
  const handleFlowCreated = useCallback(
    async (caseId: string, _company: string) => {
      setBuilderOpen(false);
      await Promise.all([
        fetchFlowCompanies().then((c) => c.length && setFlowCompanies(c)).catch(() => {}),
        fetchCases().then(setCases).catch(() => {}),
      ]);
      setAgent("flow");
      preFlowCaseIdRef.current = null; // don't restore a prior persona
      await handleSelectPersona(caseId);
    },
    [setAgent, handleSelectPersona],
  );

  const canSave = started && state.bubbles.length > 0 && !state.busy;

  const handleSave = useCallback(
    async (comment: string) => {
      if (!state.sessionId) return;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      setSaveState({ phase: "saving", message: "Saving…" });
      try {
        const res = await saveTrajectory(state.sessionId, comment);
        if (res.saved) {
          setSaveState({ phase: "saved", message: `Saved to demo-saved-trajectory/${res.path}` });
          setSaveOpen(false);
        } else {
          setSaveState({ phase: "error", message: res.reason ?? "Nothing to save yet" });
        }
      } catch (e: any) {
        setSaveState({ phase: "error", message: `Save failed: ${e?.message ?? e}` });
      }
      saveTimerRef.current = setTimeout(
        () => setSaveState({ phase: "idle", message: "" }),
        3500,
      );
    },
    [state.sessionId],
  );

  // The mic runs continuously for the whole call so the caller can talk over
  // the agent (barge-in). Two turn signals govern what we do with what it
  // hears — read through refs so the speech callbacks never go stale or churn
  // the recognizer:
  //   busy     — a turn's response stream is in flight; we can't POST another.
  //   speaking — a reply bubble is still playing its TTS (the agent's voice).
  const agentSpeaking = state.bubbles.some(
    (b) => b.kind === "reply" && b.speaking,
  );
  const busyRef = useRef(state.busy);
  busyRef.current = state.busy;
  const agentSpeakingRef = useRef(agentSpeaking);
  agentSpeakingRef.current = agentSpeaking;

  const onFinal = useCallback(
    (text: string) => {
      if (!started) return;
      // A response is still streaming — we can't start another turn yet, so
      // drop this utterance (the caller is over-running the agent's reply).
      if (busyRef.current) return;
      // Caller spoke while the agent was still talking → cut its audio.
      if (agentSpeakingRef.current) bargeIn();
      void sendUserMessage(text, true);
    },
    [started, sendUserMessage, bargeIn],
  );

  const onSpeechStart = useCallback(() => {
    // The caller started talking. If the agent is mid-sentence and we can take
    // a turn, interrupt its TTS immediately so it goes quiet like a real call.
    if (!started || busyRef.current) return;
    if (agentSpeakingRef.current) bargeIn();
  }, [started, bargeIn]);

  const callLive = started && !state.done;

  // STT engine: prefer the Chirp 3 backend (WebSocket → Silero VAD → Chirp
  // recognize); fall back to the browser Web Speech API if Chirp is
  // unsupported or its backend is unavailable (no torch / no GCP creds). Both
  // hooks expose the same shape; only the active one captures the mic (the
  // other is held idle via `enabled: false`).
  const [sttEngine, setSttEngine] = useState<"chirp" | "browser">(() =>
    isChirpSupported() ? "chirp" : "browser",
  );
  const chirpMic = useChirpSpeech({
    enabled: callLive && sttEngine === "chirp",
    onFinal,
    onSpeechStart,
    onUnavailable: () => setSttEngine("browser"),
  });
  const browserMic = useSpeechRecognition({
    enabled: callLive && sttEngine === "browser",
    onFinal,
    onSpeechStart,
  });
  const mic = sttEngine === "chirp" ? chirpMic : browserMic;

  // Mute button state shown in the control bar:
  //   muted     — caller closed the line.
  //   waiting   — agent is thinking (no audio yet); input is briefly held.
  //   listening — capturing, incl. armed for barge-in while the agent speaks.
  const micState: MicState = mic.muted
    ? "muted"
    : state.busy && !agentSpeaking
    ? "waiting"
    : "listening";

  const handleStart = async () => {
    // If the user toggled an agent OR voice the server hasn't been rebuilt with
    // yet, re-init so the live session is bound to the chosen LLM AND the text
    // gender matches the picked voice. Otherwise a stale auto-init session
    // (default agent/voice) would race the toggle — e.g. male voice + ค่ะ text.
    if (
      !state.ready
      || state.serverAgent !== state.agent
      || state.serverVoiceGender !== state.voiceGender
    ) {
      const ok = await initSession();
      if (!ok) return;
    }
    // Resolve the OS mic prompt BEFORE the call goes live, so the continuous
    // mic doesn't open into an unanswered permission dialog. If the caller
    // denies, start muted — they can grant access in the address bar and
    // unmute. When voice isn't supported at all, focus the typed fallback.
    if (mic.supported) {
      const granted = await requestMicPermission();
      if (!granted) mic.setMuted(true);
    } else {
      setTimeout(() => {
        const input = document.querySelector<HTMLInputElement>(
          ".typed-fallback input",
        );
        input?.focus();
      }, 0);
    }
    setStarted(true);
    // Outbound call: the bot greets first, before the caller says anything.
    void fireOpening();
  };

  const handleTyped = (text: string) => {
    if (!started) return;
    void sendUserMessage(text, false);
  };

  useGlobalKeyboard({
    enabled: started && !resetModalOpen,
    mic: {
      muted: mic.muted,
      toggleMute: mic.toggleMute,
      supported: mic.supported,
      error: mic.error,
      clearError: mic.clearError,
    },
    onTogglePause: togglePause,
    onRequestReset: () => setResetModalOpen(true),
    onTogglePanel: () => setPanelCollapsed((c) => !c),
    onBargeIn: bargeIn,
    isTTSPlaying: audio.isPlaying,
    done: state.done,
  });

  return (
    <div className="app">
      <CustomerPanel
        caseId={state.caseId}
        mode={state.mode}
        agent={started ? state.serverAgent : null}
        customer={state.customer}
        collapsed={panelCollapsed}
        onToggleCollapse={() => setPanelCollapsed((c) => !c)}
        headerClickable={!started}
        onHeaderClick={() => setPersonaModalOpen(true)}
      />
      <main className="chat-main">
        {started && (
          <ChatStream
            entries={state.bubbles}
            interim={mic.interim}
            started={started}
            done={state.done}
            busy={state.busy}
          />
        )}
      </main>
      <ControlBar
        started={started}
        ready={state.ready}
        starting={starting}
        startError={startError}
        onStart={handleStart}
        agent={state.agent}
        onAgentChange={handleEngineChange}
        onBuildFlow={() => setBuilderOpen(true)}
        voiceGender={state.voiceGender}
        onVoiceGenderChange={setVoiceGender}
        micState={micState}
        micSupported={mic.supported}
        micError={mic.error}
        micErrorCode={mic.errorCode}
        onClearMicError={mic.clearError}
        paused={state.paused}
        busy={state.busy}
        done={state.done}
        onToggleMic={mic.toggleMute}
        onPause={togglePause}
        onRequestReset={() => setResetModalOpen(true)}
        onTypedSubmit={handleTyped}
        onSave={() => setSaveOpen(true)}
        canSave={canSave}
        saving={saveState.phase === "saving"}
      />
      <ResetConfirmModal
        open={resetModalOpen}
        onCancel={() => setResetModalOpen(false)}
        onConfirm={() => {
          setResetModalOpen(false);
          void reset().then(() => fireOpening());  // fresh call → bot greets first
        }}
      />
      <PersonaPickerModal
        open={personaModalOpen}
        cases={pickerCases}
        currentCaseId={state.caseId}
        note={state.agent === "flow" ? "Flow mode runs the outbound-remind flow — all companies (experimental)." : undefined}
        onClose={() => setPersonaModalOpen(false)}
        onSelect={(id) => void handleSelectPersona(id)}
      />
      <FlowBuilderModal
        open={builderOpen}
        onClose={() => setBuilderOpen(false)}
        onCreated={(caseId, company) => void handleFlowCreated(caseId, company)}
      />
      {state.streamError && (
        <div className="stream-error-banner" role="alert">
          <span>{state.streamError.message}</span>
          <button
            type="button"
            onClick={() => state.streamError?.retry()}
            aria-label="Try again"
          >
            <RefreshCw size={12} aria-hidden="true" /> Try again
          </button>
          <button
            type="button"
            onClick={clearStreamError}
            aria-label="Dismiss"
          >
            <X size={12} aria-hidden="true" />
          </button>
        </div>
      )}
      {state.done && (
        <EndOfCallCard
          onRestart={() => {
            void reset().then(() => fireOpening());  // fresh call → bot greets first
          }}
          onSave={() => setSaveOpen(true)}
          saving={saveState.phase === "saving"}
        />
      )}
      <SaveDialog
        open={saveOpen}
        saving={saveState.phase === "saving"}
        onConfirm={(c) => void handleSave(c)}
        onCancel={() => setSaveOpen(false)}
      />
      {saveState.phase !== "idle" && (
        <div className={`save-toast ${saveState.phase}`} role="status">
          {saveState.message}
        </div>
      )}
      {!mic.supported && (
        <div className="info-banner" role="status">
          This browser doesn't support voice input — use the message box below.
        </div>
      )}
      {started && <ShortcutsHint />}
    </div>
  );
}
