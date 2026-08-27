import { useEffect, useState } from "react";
import { Activity, ChevronDown, ChevronUp } from "lucide-react";
import * as latency from "../latency";
import type { TurnLatency } from "../latency";

/* Always-on per-turn latency readout (four chips: VAD · STT · LLM · TTS) with an
 * expandable detail panel. Self-subscribes to the latency store so it needs no
 * props and doesn't churn the ControlBar's prop list. Shows the most recent
 * turn's numbers live as each stage reports, plus a rolling average in detail.
 *
 * Stage attribution (see latency.ts): VAD/STT/LLM are SERVER-measured (endpoint_ms
 * / recognize_ms / total_ms); TTS is CLIENT-measured first-audio. Typed turns mark
 * VAD/STT N/A. The LLM number needs the vLLM GPU to be live, else it stays pending.
 */

function fmt(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}

function chipState(
  value: number | null,
  applicable: boolean,
  hasTurn: boolean,
): { text: string; cls: string } {
  if (!applicable) return { text: "n/a", cls: "na" };
  if (value != null) return { text: fmt(value), cls: "val" };
  if (hasTurn) return { text: "···", cls: "pending" };
  return { text: "—", cls: "na" };
}

function avg(turns: TurnLatency[], pick: (t: TurnLatency) => number | null): number | null {
  const vals = turns.map(pick).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

const STAGES = [
  {
    key: "vad",
    label: "VAD",
    title: "Endpoint dead-time — trailing silence before your turn is finalized (server endpoint_ms).",
  },
  {
    key: "stt",
    label: "STT",
    title: "Speech-to-text — batch Chirp recognize() wall-time (server recognize_ms).",
  },
  {
    key: "llm",
    label: "LLM",
    title: "Model inference — sum of per-hop vLLM call times this turn (server total_ms). Needs the GPU live.",
  },
  {
    key: "tts",
    label: "TTS",
    title: "Text-to-speech — time to first audible audio (client: request → playing).",
  },
] as const;

export function LatencyMetrics() {
  const [snap, setSnap] = useState(latency.getSnapshot());
  const [open, setOpen] = useState(false);
  useEffect(() => latency.subscribe(() => setSnap(latency.getSnapshot())), []);

  const cur = snap.current;
  const hasTurn = !!cur;
  // Before any turn we don't know the modality — assume mic so the chips read as
  // applicable placeholders rather than n/a.
  const micApplicable = cur ? cur.viaMic : true;
  const vals: Record<string, number | null> = {
    vad: cur?.vadMs ?? null,
    stt: cur?.sttMs ?? null,
    llm: cur?.llmMs ?? null,
    tts: cur?.ttsMs ?? null,
  };
  const applicable: Record<string, boolean> = {
    vad: micApplicable,
    stt: micApplicable,
    llm: true,
    tts: true,
  };
  const completed = cur ? [...snap.history, cur] : snap.history;

  return (
    <div className="latency-metrics">
      <div className="latency-row">
        <Activity size={12} className="latency-icon" aria-hidden="true" />
        {STAGES.map((s) => {
          const cs = chipState(vals[s.key], applicable[s.key], hasTurn);
          return (
            <span key={s.key} className={`latency-chip ${cs.cls}`} title={s.title}>
              <span className="latency-chip-label">{s.label}</span>
              <span className="latency-chip-value">{cs.text}</span>
            </span>
          );
        })}
        <button
          type="button"
          className="latency-toggle"
          onClick={() => setOpen((o) => !o)}
          aria-label={open ? "Hide latency detail" : "Show latency detail"}
          aria-expanded={open}
          title={open ? "Hide detail" : "Show detail"}
        >
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronUp size={14} aria-hidden="true" />}
        </button>
      </div>
      {open && (
        <div className="latency-detail">
          <div className="latency-detail-grid">
            <span>End-to-end</span>
            <span>{fmt(cur?.endToEndMs)}</span>
            <span>LLM hops</span>
            <span>{cur?.llmHops ?? "—"}</span>
            <span>LLM first-hop (perceived)</span>
            <span>{fmt(cur?.llmTtftMs)}</span>
            <span>STT (perceived)</span>
            <span>{fmt(cur?.sttPerceivedMs)}</span>
          </div>
          <div className="latency-avg">
            avg / {completed.length} turn{completed.length === 1 ? "" : "s"}: VAD{" "}
            {fmt(avg(completed, (t) => t.vadMs))} · STT {fmt(avg(completed, (t) => t.sttMs))} · LLM{" "}
            {fmt(avg(completed, (t) => t.llmMs))} · TTS {fmt(avg(completed, (t) => t.ttsMs))}
          </div>
          <p className="latency-note">
            VAD/STT/LLM are server-measured; TTS is client first-audio. Stages overlap, so the
            sum is an upper bound on turn time, not the total.
          </p>
        </div>
      )}
    </div>
  );
}
