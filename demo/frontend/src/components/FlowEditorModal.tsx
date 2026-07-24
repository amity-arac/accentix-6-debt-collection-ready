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
const PHASE_CAP: Record<string, string> = { opening: "Opening", main: "Main", close: "Close" };

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

  const edit = (fn: (s: FlowSpec) => void) =>
    setSpec((prev) => {
      if (!prev) return prev;
      const next = JSON.parse(JSON.stringify(prev)) as FlowSpec;
      fn(next);
      return next;
    });
  const editState = (idx: number, fn: (st: FlowState) => void) => edit((s) => fn(s.states[idx]));

  const addState = () => {
    let i = 1;
    let id = `state${i}`;
    while (stateIds.includes(id)) id = `state${++i}`;
    edit((s) => s.states.push({ id, phase: "main", templates: [], on: [] }));
  };
  const deleteState = (idx: number) =>
    edit((s) => {
      const [removed] = s.states.splice(idx, 1);
      for (const st of s.states) st.on = (st.on ?? []).filter((t) => t.to !== removed.id);
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

  const allPhases = spec
    ? [...PHASES, ...[...new Set(spec.states.map((s) => s.phase ?? "main"))].filter((p) => !PHASES.includes(p))]
    : PHASES;
  const statesByPhase = (phase: string) =>
    spec ? spec.states.filter((s) => (s.phase ?? "main") === phase) : [];

  return (
    <div
      className={`persona-modal-backdrop${visible ? " open" : ""}`}
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="fx-modal fx-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fx-editor-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <div className="fx-head">
          <h2 id="fx-editor-title">
            แก้โครง Flow — <span className="fx-accent">{company}</span>
          </h2>
          <span className="fx-step">state machine</span>
          <button className="fx-x" onClick={onClose} aria-label="Close">
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="fx-body">
          {errors.length > 0 && (
            <div className="fx-errors" role="alert">
              {errors.map((e, i) => (
                <div key={i}>{e}</div>
              ))}
            </div>
          )}

          {!spec || !data ? (
            <p className="fx-note">กำลังโหลด…</p>
          ) : (
            <>
              {/* diagram */}
              <div className="fx-sec-title">
                แผนผัง flow<span className="fx-line" />
                <span className="fx-sec-sub">ดูอย่างเดียว · อัปเดตตามที่แก้ด้านล่าง</span>
              </div>
              <div className="fx-diagram">
                <div className="fx-lanes">
                  {allPhases.map((phase) => {
                    const states = statesByPhase(phase);
                    if (!states.length) return null;
                    return (
                      <div className="fx-lane" key={phase}>
                        <span className="fx-cap">{PHASE_CAP[phase] ?? phase}</span>
                        {states.map((st) => (
                          <div
                            className={`fx-node${st.initial ? " start" : ""}${st.terminal ? " end" : ""}`}
                            key={st.id}
                          >
                            <div className="fx-node-name">
                              {st.id}
                              {st.initial && <span className="fx-tag s">▶ เริ่ม</span>}
                              {st.terminal && <span className="fx-tag e">■ จบ</span>}
                            </div>
                            {(st.on ?? []).map((t, i) => (
                              <div className="fx-edge" key={i}>
                                <em>{t.event}</em>
                                <span className="fx-arr">→</span>
                                <b>{t.to}</b>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* events */}
              <div className="fx-sec-title">
                Events<span className="fx-line" />
                <button className="fx-btn fx-mini" onClick={addEvent}>
                  <Plus size={12} /> event
                </button>
              </div>
              <div className="fx-chips">
                {events.map((ev) => (
                  <span className="fx-chip fx-event" key={ev}>
                    {ev}
                    <button className="fx-rm" onClick={() => removeEvent(ev)} aria-label={`remove ${ev}`}>
                      <X size={11} />
                    </button>
                  </span>
                ))}
              </div>

              {/* states */}
              <div className="fx-sec-title">
                States<span className="fx-line" />
                <button className="fx-btn fx-mini" onClick={addState}>
                  <Plus size={12} /> เพิ่ม state
                </button>
              </div>
              {spec.states.map((st, idx) => (
                <div className="fx-state" key={st.id}>
                  <div className="fx-state-head">
                    <span className="fx-sid">{st.id}</span>
                    <select
                      value={st.phase ?? "main"}
                      onChange={(e) => editState(idx, (s) => (s.phase = e.target.value))}
                    >
                      {allPhases.map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                    <label>
                      <input
                        type="checkbox"
                        checked={!!st.initial}
                        onChange={(e) => editState(idx, (s) => (s.initial = e.target.checked))}
                      />{" "}
                      เริ่มต้น
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={!!st.terminal}
                        onChange={(e) => editState(idx, (s) => (s.terminal = e.target.checked))}
                      />{" "}
                      จบสาย
                    </label>
                    <span className="fx-grow" />
                    <button
                      className="fx-btn fx-mini fx-danger"
                      onClick={() => deleteState(idx)}
                      aria-label={`delete ${st.id}`}
                    >
                      <Trash2 size={12} /> ลบ
                    </button>
                  </div>
                  <div className="fx-state-rows">
                    <div className="fx-erow">
                      <span className="fx-erow-lbl">พูด</span>
                      <div className="fx-chips">
                        {(st.templates ?? []).map((t, ti) => (
                          <span className="fx-chip" key={ti}>
                            {t.fine_state}
                            <button
                              className="fx-rm"
                              onClick={() => editState(idx, (s) => s.templates!.splice(ti, 1))}
                              aria-label="remove beat"
                            >
                              <X size={11} />
                            </button>
                          </span>
                        ))}
                        <select
                          className="fx-add-select"
                          value=""
                          onChange={(e) => {
                            const fs = e.target.value;
                            if (fs)
                              editState(idx, (s) => (s.templates = [...(s.templates ?? []), { fine_state: fs }]));
                          }}
                        >
                          <option value="">＋ เพิ่มบท…</option>
                          {data.fine_states.map((fs) => (
                            <option key={fs} value={fs}>{fs}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <div className="fx-erow">
                      <span className="fx-erow-lbl">เมื่อ</span>
                      <div className="fx-trans">
                        {(st.on ?? []).map((t, ti) => (
                          <div className="fx-t" key={ti}>
                            <select
                              value={t.event}
                              onChange={(e) => editState(idx, (s) => (s.on![ti].event = e.target.value))}
                            >
                              {events.map((ev) => (
                                <option key={ev} value={ev}>{ev}</option>
                              ))}
                            </select>
                            <span className="fx-arr">→</span>
                            <select
                              value={t.to}
                              onChange={(e) => editState(idx, (s) => (s.on![ti].to = e.target.value))}
                            >
                              {stateIds.map((sid) => (
                                <option key={sid} value={sid}>{sid}</option>
                              ))}
                            </select>
                            <button
                              className="fx-rm"
                              onClick={() => editState(idx, (s) => s.on!.splice(ti, 1))}
                              aria-label="remove transition"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        ))}
                        <button
                          className="fx-btn fx-mini"
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
                </div>
              ))}

              <div className="fx-callout">
                ℹ️ ขั้นสูง (tools / constraints / FAQ routing) แก้ผ่านไฟล์ JSON — editor นี้โฟกัสที่
                state machine (states · beats · transitions · events)
              </div>
            </>
          )}
        </div>

        <div className="fx-foot">
          <span className="fx-foot-hint">
            บันทึกแล้ว validate อัตโนมัติ — ถ้าผิด (เช่น transition ชี้ state ที่ไม่มี) จะเตือน ไม่เขียนทับ
          </span>
          <span className="fx-spacer" />
          <button className="fx-btn fx-ghost" onClick={onClose}>
            ยกเลิก
          </button>
          <button className="fx-btn fx-primary" onClick={() => void save()} disabled={saving || !spec}>
            {saving ? "กำลังบันทึก…" : "บันทึก flow"}
          </button>
        </div>
      </div>
    </div>
  );
}
