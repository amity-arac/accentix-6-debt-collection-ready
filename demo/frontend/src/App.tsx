import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { CustomerPanel } from "./components/CustomerPanel";
import { ChatStream } from "./components/ChatStream";
import { ControlBar } from "./components/ControlBar";
import { ResetConfirmModal } from "./components/ResetConfirmModal";
import { PersonaPickerModal } from "./components/PersonaPickerModal";
import { FlowBuilderModal } from "./components/FlowBuilderModal";
import { FlowUploadModal } from "./components/FlowUploadModal";
import { CompanySelect } from "./components/CompanySelect";
import { ModeSelect } from "./components/ModeSelect";
import { InstructionModal } from "./components/InstructionModal";
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
  fetchFlowCompaniesMeta,
  deleteFlowCompany,
  saveTrajectory,
  type Engine,
  type PersonaCase,
} from "./api";
import * as audio from "./audio";

type SaveState = { phase: "idle" | "saving" | "saved" | "error"; message: string };

// Fallback flow-supported companies until /api/flow/companies responds.
const FLOW_COMPANIES_DEFAULT = ["AEON", "KBANK", "SKL", "AMT"];

export default function App() {
  const {
    state,
    start,
    setAgent,
    setModel,
    setVoiceGender,
    selectCase,
    fireOpening,
    sendUserMessage,
    reset,
    togglePause,
    bargeIn,
    clearStreamError,
  } = useSession();
  // App shell: company → mode → playground. Editor/builder are modal overlays.
  const [screen, setScreen] = useState<"company" | "mode" | "play">("company");
  const [company, setCompany] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string>("");
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [resetModalOpen, setResetModalOpen] = useState(false);
  const [personaModalOpen, setPersonaModalOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [cases, setCases] = useState<PersonaCase[]>([]);
  const [flowCompanies, setFlowCompanies] = useState<string[]>(FLOW_COMPANIES_DEFAULT);
  // which of them the server will let us delete (Builder-created only)
  const [deletableCompanies, setDeletableCompanies] = useState<string[]>([]);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [instrOpen, setInstrOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ phase: "idle", message: "" });
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

  // No auto-init: the session is created when the user enters the Playground
  // for a chosen company (see enterPlayground). The landing is company-select.

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
    void fetchFlowCompaniesMeta()
      .then((rows) => {
        if (!cancelled) setDeletableCompanies(rows.filter((r) => r.deletable).map((r) => r.company));
      })
      .catch(() => {
        /* no delete affordance is the safe degradation */
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

  // Engine toggle inside the Playground — company is fixed by the shell, so this
  // just swaps the driving LLM (re-init happens on Start if it changed).
  const handleEngineChange = useCallback((e: Engine) => setAgent(e), [setAgent]);

  // Playground picker offers only the selected company's personas.
  const pickerCases = company ? cases.filter((c) => c.company === company) : cases;

  // Enter the Playground for a company: default to the Flow engine, load the
  // company's first persona (creates the session), show the setup screen.
  const enterPlayground = useCallback(
    async (co: string) => {
      setCompany(co);
      setAgent("qwen");
      setStarted(false);
      setScreen("play");
      const first = cases.find((c) => c.company === co);
      if (first) await handleSelectPersona(first.id);
    },
    [cases, setAgent, handleSelectPersona],
  );

  const backToMenu = useCallback(() => {
    setStarted(false);
    setScreen(company ? "mode" : "company");
  }, [company]);

  // After the Builder creates a company: refresh, then drop into its Playground.
  const handleFlowCreated = useCallback(
    async (caseId: string, newCompany: string) => {
      setBuilderOpen(false);
      await Promise.all([
        fetchFlowCompanies().then((c) => c.length && setFlowCompanies(c)).catch(() => {}),
        fetchFlowCompaniesMeta()
          .then((r) => setDeletableCompanies(r.filter((x) => x.deletable).map((x) => x.company)))
          .catch(() => {}),
        fetchCases().then(setCases).catch(() => {}),
      ]);
      setCompany(newCompany);
      setAgent("qwen");
      setStarted(false);
      setScreen("play");
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
    <>
      {screen === "company" && (
        <CompanySelect
          companies={flowCompanies}
          cases={cases}
          deletable={deletableCompanies}
          onPick={(co) => { setCompany(co); setScreen("mode"); }}
          onNew={() => setUploadOpen(true)}
          onDelete={async (co) => {
            const res = await deleteFlowCompany(co);
            if (!res.ok) {
              window.alert(res.errors?.join("\n") ?? `ลบ ${co} ไม่สำเร็จ`);
              return;
            }
            // the company is gone server-side; drop it from every list that named it
            setFlowCompanies((prev) => prev.filter((c) => c !== co));
            setDeletableCompanies((prev) => prev.filter((c) => c !== co));
            setCases((prev) => prev.filter((c) => c.company !== co));
            if (company === co) { setCompany(null); setScreen("company"); }
          }}
        />
      )}

      {screen === "mode" && company && (
        <ModeSelect
          company={company}
          onPlay={() => void enterPlayground(company)}
          onView={() => setInstrOpen(true)}
          onBack={() => setScreen("company")}
        />
      )}

      {screen === "play" && (
        <div className="app">
          <button className="pg-back" onClick={backToMenu} title="กลับเมนู">← เมนู</button>
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
            model={state.model}
            onModelChange={setModel}
            company={company}
            voiceMode={state.voiceMode}
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
          {state.streamError && (
            <div className="stream-error-banner" role="alert">
              <span>{state.streamError.message}</span>
              <button type="button" onClick={() => state.streamError?.retry()} aria-label="Try again">
                <RefreshCw size={12} aria-hidden="true" /> Try again
              </button>
              <button type="button" onClick={clearStreamError} aria-label="Dismiss">
                <X size={12} aria-hidden="true" />
              </button>
            </div>
          )}
          {state.done && (
            <EndOfCallCard
              onRestart={() => { void reset().then(() => fireOpening()); }}
              onSave={() => setSaveOpen(true)}
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
      )}

      <ResetConfirmModal
        open={resetModalOpen}
        onCancel={() => setResetModalOpen(false)}
        onConfirm={() => {
          setResetModalOpen(false);
          void reset().then(() => fireOpening());
        }}
      />
      <PersonaPickerModal
        open={personaModalOpen}
        cases={pickerCases}
        currentCaseId={state.caseId}
        note={company ? `personas ของ ${company}` : undefined}
        onClose={() => setPersonaModalOpen(false)}
        onSelect={(id) => void handleSelectPersona(id)}
      />
      <FlowBuilderModal
        open={builderOpen}
        onClose={() => setBuilderOpen(false)}
        onCreated={(caseId, co) => void handleFlowCreated(caseId, co)}
      />
      <FlowUploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onCreated={(caseId, co) => {
          setUploadOpen(false);
          void handleFlowCreated(caseId, co);
        }}
      />
      <InstructionModal
        open={instrOpen}
        company={company}
        onClose={() => setInstrOpen(false)}
      />
      <SaveDialog
        open={saveOpen}
        saving={saveState.phase === "saving"}
        onConfirm={(c) => void handleSave(c)}
        onCancel={() => setSaveOpen(false)}
      />
    </>
  );
}
