import { useEffect, useMemo, useRef, useState } from "react";
import { X, Plus, Trash2 } from "lucide-react";
import {
  fetchFlowSpec,
  saveFlowSpec,
  type FlowSpec,
  type FlowSpecData,
  type FlowState,
} from "../api";
import { useMountTransition } from "../hooks/useMountTransition";

const MODAL_EXIT_MS = 380;
const PHASES = ["opening", "main", "close"];

type Props = {
  open: boolean;
  company: string | null;
  onClose: () => void;
  onSaved: (company: string) => void;
};

export function FlowEditorModal({ open, company, onClose, onSaved }: Props) {
  const { mounted, visible } = useMountTransition(open, MODAL_EXIT_MS);
  const [data, setData] = useState<FlowSpecData | null>(null);
  const [spec, setSpec] = useState<FlowSpec | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!mounted || !company) return;
    setData(null);
    setSpec(null);
    setErrors([]);
    void fetchFlowSpec(company)
      .then((d) => {
        setData(d);
        setSpec(JSON.parse(JSON.stringify(d.spec)) as FlowSpec);
      })
      .catch(() => setErrors([`โหลด spec ของ ${company} ไม่สำเร็จ`]));
  }, [mounted, company]);

  useEffect(() => {
    if (mounted) dialogRef.current?.focus();
  }, [mounted]);

  const stateIds = useMemo(() => (spec ? spec.states.map((s) => s.id) : []), [spec]);
  const events = useMemo(() => (spec ? Object.keys(spec.events ?? {}) : []), [spec]);

  if (!mounted) return null;

  // All edits go through a clone → keeps React state immutable.
  const edit = (fn: (s: FlowSpec) => void) =>
    setSpec((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev)) as FlowSpec;
      fn(next);
      return next;
    });

  const editState = (idx: number, fn: (st: FlowState) => void) =>
    edit((s) => fn(s.states[idx]));

  const addState = () => {
    const base = "state";
    let i = 1;
    let id = `${base}${i}`;
    while (stateIds.includes(id)) id = `${base}${++i}`;
    edit((s) => s.states.push({ id, phase: "main", templates: [], on: [] }));
  };

  const deleteState = (idx: number) =>
    edit((s) => {
      const [removed] = s.states.splice(idx, 1);
      // cascade: drop transitions pointing at the deleted state
      for (const st of s.states)
        st.on = (st.on ?? []).filter((t) => t.to !== removed.id);
    });

  const addEvent = () => {
    const name = window.prompt("ชื่อ event ใหม่ (เช่น refuses, hardship):")?.trim();
    if (!name) return;
    edit((s) => {
      s.events = s.events ?? {};
      if (!s.events[name]) s.events[name] = { desc: "" };
    });
  };

  const removeEvent = (name: string) =>
    edit((s) => {
      if (s.events) delete s.events[name];
      for (const st of s.states) st.on = (st.on ?? []).filter((t) => t.event !== name);
    });

  const save = async () => {
    if (!spec || !company) return;
    setErrors([]);
    setSaving(true);
    try {
      const res = await saveFlowSpec(company, spec);
      if (res.ok) onSaved(company);
      else setErrors(res.errors ?? ["บันทึกไม่สำเร็จ"]);
    } catch (e: any) {
      setErrors([`บันทึกไม่สำเร็จ: ${e?.message ?? e}`]);
    } finally {
      setSaving(false);
    }
  };

  const statesByPhase = (phase: string) =>
    spec ? spec.states.filter((s) => (s.phase ?? "main") === phase) : [];
  const otherPhases = spec
    ? [...new Set(spec.states.map((s) => s.phase ?? "main"))].filter((p) => !PHASES.includes(p))
    : [];

  return (
    <div
      className={`persona-modal-backdrop${visible ? " open" : ""}`}
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="persona-modal flow-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="flow-editor-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <header className="persona-modal-head">
          <h2 id="flow-editor-title" className="persona-modal-title">
            แก้ flow: {company}
          </h2>
          <button type="button" className="persona-modal-close" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {errors.length > 0 && (
          <div className="mic-error" role="alert" style={{ margin: "0 16px 8px" }}>
            <span>{errors.join(" · ")}</span>
          </div>
        )}

        {!spec || !data ? (
          <p className="persona-modal-note">กำลังโหลด…</p>
        ) : (
          <div className="persona-modal-body flow-editor-body">
            {/* --- read-only diagram --- */}
            <div className="flow-diagram">
              {[...PHASES, ...otherPhases].map((phase) => {
                const states = statesByPhase(phase);
                if (!states.length) return null;
                return (
                  <div key={phase} className="flow-diagram-col">
                    <div className="flow-diagram-phase">{phase.toUpperCase()}</div>
                    {states.map((st) => (
                      <div key={st.id} className="flow-diagram-node">
                        <div className="flow-node-id">
                          {st.id}
                          {st.initial && <span className="flow-badge">▶ start</span>}
                          {st.terminal && <span className="flow-badge">■ end</span>}
                        </div>
                        {(st.on ?? []).map((t, i) => (
                          <div key={i} className="flow-node-edge">
                            {t.event} → <b>{t.to}</b>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>

            {/* --- events --- */}
            <div className="flow-section">
              <div className="flow-section-head">
                <span>Events</span>
                <button type="button" className="btn-mini" onClick={addEvent}>
                  <Plus size={12} /> event
                </button>
              </div>
              <div className="flow-chips">
                {events.map((ev) => (
                  <span key={ev} className="flow-chip">
                    {ev}
                    <button type="button" onClick={() => removeEvent(ev)} aria-label={`remove ${ev}`}>
                      <X size={11} />
                    </button>
                  </span>
                ))}
              </div>
            </div>

            {/* --- states --- */}
            <div className="flow-section">
              <div className="flow-section-head">
                <span>States</span>
                <button type="button" className="btn-mini" onClick={addState}>
                  <Plus size={12} /> state
                </button>
              </div>
              {spec.states.map((st, idx) => (
                <div key={st.id} className="flow-state-card">
                  <div className="flow-state-top">
                    <code>{st.id}</code>
                    <select
                      value={st.phase ?? "main"}
                      onChange={(e) => editState(idx, (s) => (s.phase = e.target.value))}
                    >
                      {[...PHASES, ...otherPhases].map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                    <label>
                      <input
                        type="checkbox"
                        checked={!!st.initial}
                        onChange={(e) => editState(idx, (s) => (s.initial = e.target.checked))}
                      />{" "}
                      start
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={!!st.terminal}
                        onChange={(e) => editState(idx, (s) => (s.terminal = e.target.checked))}
                      />{" "}
                      end
                    </label>
                    <button
                      type="button"
                      className="btn-mini danger"
                      onClick={() => deleteState(idx)}
                      aria-label={`delete ${st.id}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>

                  {/* beats (fine_state templates) */}
                  <div className="flow-row">
                    <span className="flow-row-label">beats</span>
                    <div className="flow-chips">
                      {(st.templates ?? []).map((t, ti) => (
                        <span key={ti} className="flow-chip">
                          {t.fine_state}
                          <button
                            type="button"
                            onClick={() => editState(idx, (s) => s.templates!.splice(ti, 1))}
                            aria-label="remove beat"
                          >
                            <X size={11} />
                          </button>
                        </span>
                      ))}
                      <select
                        value=""
                        onChange={(e) => {
                          const fs = e.target.value;
                          if (fs) editState(idx, (s) => (s.templates = [...(s.templates ?? []), { fine_state: fs }]));
                        }}
                      >
                        <option value="">+ beat…</option>
                        {data.fine_states.map((fs) => (
                          <option key={fs} value={fs}>{fs}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* transitions */}
                  <div className="flow-row">
                    <span className="flow-row-label">on</span>
                    <div className="flow-transitions">
                      {(st.on ?? []).map((t, ti) => (
                        <div key={ti} className="flow-transition">
                          <select
                            value={t.event}
                            onChange={(e) => editState(idx, (s) => (s.on![ti].event = e.target.value))}
                          >
                            {events.map((ev) => (
                              <option key={ev} value={ev}>{ev}</option>
                            ))}
                          </select>
                          <span>→</span>
                          <select
                            value={t.to}
                            onChange={(e) => editState(idx, (s) => (s.on![ti].to = e.target.value))}
                          >
                            {stateIds.map((sid) => (
                              <option key={sid} value={sid}>{sid}</option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => editState(idx, (s) => s.on!.splice(ti, 1))}
                            aria-label="remove transition"
                          >
                            <X size={11} />
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        className="btn-mini"
                        onClick={() =>
                          editState(idx, (s) => {
                            s.on = s.on ?? [];
                            s.on.push({ event: events[0] ?? "", to: stateIds[0] ?? st.id });
                          })
                        }
                        disabled={!events.length}
                      >
                        <Plus size={12} /> transition
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="persona-detail-actions" style={{ padding: "12px 16px" }}>
          <button
            type="button"
            className="btn persona-select-btn"
            onClick={() => void save()}
            disabled={saving || !spec}
          >
            {saving ? "กำลังบันทึก…" : "บันทึก flow"}
          </button>
        </div>
      </div>
    </div>
  );
}
