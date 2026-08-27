import { useEffect, useMemo, useRef, useState } from "react";
import { X, Wand2, Plus } from "lucide-react";
import { fetchFlowBeats, createFlowCompany, type FlowBeat } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";

const MODAL_EXIT_MS = 380;
const BASE_COMPANY_NAME = "อิอ้อน"; // AEON name baked into the example templates

// Phase → Thai section header + display order.
const PHASE_GROUPS: { key: string; label: string }[] = [
  { key: "opening", label: "เปิดสาย (Opening)" },
  { key: "main", label: "คุยหลัก (Main)" },
  { key: "close", label: "ปิดสาย (Close)" },
  { key: "faq", label: "คำถามแทรก (FAQ)" },
  { key: "aux", label: "ตามบริบท (Auxiliary)" },
];

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (caseId: string, company: string) => void;
};

export function FlowBuilderModal({ open, onClose, onCreated }: Props) {
  const { mounted, visible } = useMountTransition(open, MODAL_EXIT_MS);
  const [beats, setBeats] = useState<FlowBeat[]>([]);
  const [company, setCompany] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [agentName, setAgentName] = useState("");
  const [templates, setTemplates] = useState<Record<string, string>>({});
  const [excluded, setExcluded] = useState<Record<string, boolean>>({});
  const [custom, setCustom] = useState<{ fine_state: string; phase: string; template: string }[]>([]);
  const [composerPhase, setComposerPhase] = useState<string | null>(null);
  const [cFs, setCFs] = useState("");
  const [cText, setCText] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!mounted || beats.length) return;
    void fetchFlowBeats()
      .then((b) => {
        setBeats(b);
        setTemplates(Object.fromEntries(b.map((x) => [x.fine_state, x.example])));
      })
      .catch(() => setErrors(["โหลด beats ไม่สำเร็จ — backend flow endpoint พร้อมไหม?"]));
  }, [mounted, beats.length]);

  useEffect(() => {
    if (mounted) dialogRef.current?.focus();
  }, [mounted]);

  const filledCount = useMemo(
    () =>
      beats.filter((b) => !excluded[b.fine_state] && (templates[b.fine_state] ?? "").trim()).length +
      custom.length,
    [beats, templates, excluded, custom],
  );
  const total = beats.length + custom.length;
  const pct = total ? Math.round((filledCount / total) * 100) : 0;
  const byPhase = (key: string) => beats.filter((b) => b.phase === key);
  const customByPhase = (key: string) => custom.filter((c) => c.phase === key);
  // Custom beats can only bind to states that exist in the flow (opening/main/close).
  const CAN_ADD = new Set(["opening", "main", "close"]);

  if (!mounted) return null;

  const setBeat = (fs: string, v: string) => setTemplates((t) => ({ ...t, [fs]: v }));
  const toggleExclude = (fs: string) => setExcluded((e) => ({ ...e, [fs]: !e[fs] }));

  const openComposer = (phase: string) => {
    setComposerPhase(phase);
    setCFs("");
    setCText("");
  };
  const addCustom = (phase: string) => {
    const fs = cFs.trim();
    const text = cText.trim();
    if (!/^[a-z][a-z0-9_]*$/.test(fs)) {
      setErrors(["ชื่อ beat ใช้ได้แค่ a-z / 0-9 / _ ขึ้นต้นด้วยตัวอักษร"]);
      return;
    }
    if (!text) {
      setErrors(["ใส่ข้อความของ beat ด้วย"]);
      return;
    }
    if (beats.some((b) => b.fine_state === fs) || custom.some((c) => c.fine_state === fs)) {
      setErrors([`มี beat ชื่อ ${fs} อยู่แล้ว`]);
      return;
    }
    setErrors([]);
    setCustom((c) => [...c, { fine_state: fs, phase, template: text }]);
    setComposerPhase(null);
  };
  const removeCustom = (fs: string) => setCustom((c) => c.filter((x) => x.fine_state !== fs));

  const applyName = () => {
    if (!displayName.trim()) return;
    setTemplates((t) => {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(t))
        out[k] = v.split(BASE_COMPANY_NAME).join(displayName.trim());
      return out;
    });
  };

  const submit = async () => {
    setErrors([]);
    setSaving(true);
    try {
      // Excluded beats → sent empty so the backend trims them from the flow.
      const outTemplates = Object.fromEntries(
        Object.entries(templates).map(([k, v]) => [k, excluded[k] ? "" : v]),
      );
      const res = await createFlowCompany({
        company: company.trim().toUpperCase(),
        display_name: displayName.trim(),
        agent_name: agentName.trim(),
        templates: outTemplates,
        custom,
      });
      if (res.ok && res.case_id && res.company) onCreated(res.case_id, res.company);
      else setErrors(res.errors ?? ["สร้างไม่สำเร็จ"]);
    } catch (e: any) {
      setErrors([`สร้างไม่สำเร็จ: ${e?.message ?? e}`]);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className={`persona-modal-backdrop${visible ? " open" : ""}`}
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="fx-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fx-builder-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <div className="fx-head">
          <h2 id="fx-builder-title">สร้างบริษัทใหม่สำหรับ Flow</h2>
          <span className="fx-step">ข้อมูลบริษัท + บทพูด</span>
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

          <div className="fx-id-grid">
            <div className="fx-field">
              <label>รหัส (A–Z)</label>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value.toUpperCase())}
                placeholder="SCB"
                maxLength={12}
              />
            </div>
            <div className="fx-field">
              <label>ชื่อบริษัท (ไทย)</label>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="ไทยพาณิชย์"
              />
            </div>
            <div className="fx-field">
              <label>ชื่อเจ้าหน้าที่</label>
              <input
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="น้องเอสซีบี"
              />
            </div>
          </div>

          <div className="fx-autofill">
            <span>
              ✨ กด <b>ใส่ชื่อบริษัทให้อัตโนมัติ</b> เพื่อแทน “{BASE_COMPANY_NAME}” ในทุกช่องด้วยชื่อของคุณ
            </span>
            <button className="fx-btn fx-mini" onClick={applyName} disabled={!displayName.trim()}>
              <Wand2 size={13} aria-hidden="true" /> ใส่ชื่ออัตโนมัติ
            </button>
          </div>

          <div className="fx-progress">
            <span className="fx-n">กรอกแล้ว</span>
            <div className="fx-bar">
              <i style={{ width: `${pct}%` }} />
            </div>
            <span className="fx-n">
              {filledCount} / {total} จังหวะ
            </span>
          </div>
          <p className="fx-note">
            แก้ข้อความแต่ละจังหวะ · <b>“✕ ไม่ใช้”</b> เพื่อตัดจังหวะออก · <b>“＋ เพิ่มบทเอง”</b> เพื่อสร้างบทใหม่
          </p>

          {PHASE_GROUPS.map((g, gi) => {
            const items = byPhase(g.key);
            const customs = customByPhase(g.key);
            const canAdd = CAN_ADD.has(g.key);
            if (!items.length && !customs.length && !canAdd) return null;
            const filled =
              items.filter((b) => !excluded[b.fine_state] && (templates[b.fine_state] ?? "").trim()).length +
              customs.length;
            return (
              <details className="fx-phase" key={g.key} open={gi < 2}>
                <summary>
                  <span className="fx-caret">▶</span> {g.label}
                  <span className="fx-cnt">
                    {filled}/{items.length + customs.length}
                  </span>
                </summary>
                <div className="fx-phase-inner">
                  {items.map((b) => {
                    const val = templates[b.fine_state] ?? "";
                    const isFilled = !!val.trim();
                    const off = !!excluded[b.fine_state];
                    return (
                      <div className={`fx-beat${off ? " fx-off" : ""}`} key={b.fine_state}>
                        <div className="fx-beat-top">
                          <span className="fx-what">{b.label}</span>
                          <code>{b.fine_state}</code>
                          {b.required ? (
                            <span className="fx-req">ต้องมี</span>
                          ) : off ? (
                            <span className="fx-empty">ตัดออกแล้ว</span>
                          ) : isFilled ? (
                            <span className="fx-filled">✓ กรอกแล้ว</span>
                          ) : (
                            <span className="fx-empty">เว้นว่าง = ตัดออก</span>
                          )}
                          {!b.required && (
                            <button
                              type="button"
                              className="fx-beat-x"
                              onClick={() => toggleExclude(b.fine_state)}
                            >
                              {off ? "↺ ใช้" : "✕ ไม่ใช้"}
                            </button>
                          )}
                        </div>
                        {!off && (
                          <textarea
                            value={val}
                            onChange={(e) => setBeat(b.fine_state, e.target.value)}
                            rows={2}
                            placeholder={b.example}
                          />
                        )}
                      </div>
                    );
                  })}

                  {customs.map((c) => (
                    <div className="fx-beat" key={c.fine_state}>
                      <div className="fx-beat-top">
                        <span className="fx-what">บทที่เพิ่มเอง</span>
                        <code>{c.fine_state}</code>
                        <span className="fx-new">ใหม่</span>
                        <button
                          type="button"
                          className="fx-beat-x"
                          onClick={() => removeCustom(c.fine_state)}
                        >
                          ✕ ลบ
                        </button>
                      </div>
                      <textarea
                        value={c.template}
                        rows={2}
                        onChange={(e) =>
                          setCustom((arr) =>
                            arr.map((x) =>
                              x.fine_state === c.fine_state ? { ...x, template: e.target.value } : x,
                            ),
                          )
                        }
                      />
                    </div>
                  ))}

                  {canAdd &&
                    (composerPhase === g.key ? (
                      <div className="fx-composer">
                        <input
                          placeholder="ชื่อ beat (เช่น offer_promo)"
                          value={cFs}
                          onChange={(e) => setCFs(e.target.value)}
                        />
                        <textarea
                          placeholder="ข้อความ (ใช้ {customer_name} {amount} {suffix} ได้)"
                          value={cText}
                          rows={2}
                          onChange={(e) => setCText(e.target.value)}
                        />
                        <div className="fx-composer-row">
                          <button className="fx-btn fx-mini" onClick={() => addCustom(g.key)}>
                            เพิ่มบท
                          </button>
                          <button
                            className="fx-btn fx-mini fx-ghost"
                            onClick={() => setComposerPhase(null)}
                          >
                            ยกเลิก
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="fx-btn fx-mini fx-addbeat"
                        onClick={() => openComposer(g.key)}
                      >
                        <Plus size={12} aria-hidden="true" /> เพิ่มบทเอง
                      </button>
                    ))}
                </div>
              </details>
            );
          })}
        </div>

        <div className="fx-foot">
          <span className="fx-foot-hint">
            ต้องมีอย่างน้อย <b>greet_verify</b> · ที่เหลือเว้นว่างได้
          </span>
          <span className="fx-spacer" />
          <button className="fx-btn fx-ghost" onClick={onClose}>
            ยกเลิก
          </button>
          <button
            className="fx-btn fx-primary"
            onClick={() => void submit()}
            disabled={saving || !company.trim() || !displayName.trim()}
          >
            <Plus size={15} aria-hidden="true" /> {saving ? "กำลังสร้าง…" : "สร้าง + เล่นเลย"}
          </button>
        </div>
      </div>
    </div>
  );
}
