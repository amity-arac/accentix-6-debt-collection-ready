import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { CustomerPanel } from "./components/CustomerPanel";
import { ChatStream } from "./components/ChatStream";
import { ControlBar } from "./components/ControlBar";
import { ResetConfirmModal } from "./components/ResetConfirmModal";
import { PersonaPickerModal } from "./components/PersonaPickerModal";
import { EndOfCallCard } from "./components/EndOfCallCard";
import { ShortcutsHint } from "./components/ShortcutsHint";
import { useSession } from "./hooks/useSession";
import { useSpeechRecognition } from "./hooks/useSpeechRecognition";
import { useGlobalKeyboard } from "./hooks/useGlobalKeyboard";
import { requestMicPermission } from "./speech";
import { fetchCases, saveTrajectory, type PersonaCase } from "./api";
import * as audio from "./audio";

type SaveState = { phase: "idle" | "saving" | "saved" | "error"; message: string };

export default function App() {
  const {
    state,
    start,
    setAgent,
    selectCase,
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
  const [cases, setCases] = useState<PersonaCase[]>([]);
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

  const canSave = started && state.bubbles.length > 0 && !state.busy;

  const handleSave = useCallback(async () => {
    if (!state.sessionId) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveState({ phase: "saving", message: "Saving…" });
    try {
      const res = await saveTrajectory(state.sessionId);
      if (res.saved) {
        setSaveState({ phase: "saved", message: `Saved to demo-saved-trajectory/${res.path}` });
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
  }, [state.sessionId]);

  const onFinal = useCallback(
    (text: string) => {
      if (!started) return;
      void sendUserMessage(text, true);
    },
    [sendUserMessage, started],
  );

  const mic = useSpeechRecognition(onFinal);

  const handleStart = async () => {
    // If the user toggled to an agent the server hasn't been rebuilt with
    // yet, re-init so the live session is bound to the chosen LLM. Otherwise
    // a stale auto-init session (default agent) would race the toggle.
    if (!state.ready || state.serverAgent !== state.agent) {
      const ok = await initSession();
      if (!ok) return;
    }
    setStarted(true);
    // Pre-warm the mic permission on stage so the first Space-hold isn't
    // eaten by the permission prompt.
    if (mic.supported) {
      void requestMicPermission();
    } else {
      // Voice input isn't available; nudge focus to the typed fallback so
      // the presenter can start typing immediately.
      setTimeout(() => {
        const input = document.querySelector<HTMLInputElement>(
          ".typed-fallback input",
        );
        input?.focus();
      }, 0);
    }
  };

  const handleToggleMic = () => {
    if (mic.state === "idle") bargeIn();
    mic.toggle();
  };

  const handleTyped = (text: string) => {
    if (!started) return;
    void sendUserMessage(text, false);
  };

  useGlobalKeyboard({
    enabled: started && !resetModalOpen,
    mic: {
      state: mic.state,
      start: mic.start,
      stop: mic.stop,
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
        onAgentChange={setAgent}
        micState={mic.state}
        micSupported={mic.supported}
        micError={mic.error}
        micErrorCode={mic.errorCode}
        onClearMicError={mic.clearError}
        paused={state.paused}
        busy={state.busy}
        done={state.done}
        onToggleMic={handleToggleMic}
        onPause={togglePause}
        onRequestReset={() => setResetModalOpen(true)}
        onTypedSubmit={handleTyped}
        onSave={() => void handleSave()}
        canSave={canSave}
        saving={saveState.phase === "saving"}
      />
      <ResetConfirmModal
        open={resetModalOpen}
        onCancel={() => setResetModalOpen(false)}
        onConfirm={() => {
          setResetModalOpen(false);
          void reset();
        }}
      />
      <PersonaPickerModal
        open={personaModalOpen}
        cases={cases}
        currentCaseId={state.caseId}
        onClose={() => setPersonaModalOpen(false)}
        onSelect={(id) => void handleSelectPersona(id)}
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
            void reset();
          }}
          onSave={() => void handleSave()}
          saving={saveState.phase === "saving"}
        />
      )}
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
