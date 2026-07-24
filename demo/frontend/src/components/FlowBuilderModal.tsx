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
    () => Object.values(templates).filter((t) => t && t.trim()).length,
    [templates],
  );
  const pct = beats.length ? Math.round((filledCount / beats.length) * 100) : 0;
  const byPhase = (key: string) => beats.filter((b) => b.phase === key);

  if (!mounted) return null;

  const setBeat = (fs: string, v: string) => setTemplates((t) => ({ ...t, [fs]: v }));

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
      const res = await createFlowCompany({
        company: company.trim().toUpperCase(),
        display_name: displayName.trim(),
        agent_name: agentName.trim(),
        templates,
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
              {filledCount} / {beats.length} จังหวะ
            </span>
          </div>
          <p className="fx-note">
            แก้ข้อความแต่ละจังหวะได้เลย — <b>ช่องที่เว้นว่างจะถูกตัดออกจาก flow เอง</b>
          </p>

          {PHASE_GROUPS.map((g, gi) => {
            const items = byPhase(g.key);
            if (!items.length) return null;
            const filled = items.filter((b) => (templates[b.fine_state] ?? "").trim()).length;
            return (
              <details className="fx-phase" key={g.key} open={gi < 2}>
                <summary>
                  <span className="fx-caret">▶</span> {g.label}
                  <span className="fx-cnt">
                    {filled}/{items.length}
                  </span>
                </summary>
                <div className="fx-phase-inner">
                  {items.map((b) => {
                    const val = templates[b.fine_state] ?? "";
                    const isFilled = !!val.trim();
                    return (
                      <div className="fx-beat" key={b.fine_state}>
                        <div className="fx-beat-top">
                          <span className="fx-what">{b.label}</span>
                          <code>{b.fine_state}</code>
                          {b.required ? (
                            <span className="fx-req">ต้องมี</span>
                          ) : isFilled ? (
                            <span className="fx-filled">✓ กรอกแล้ว</span>
                          ) : (
                            <span className="fx-empty">เว้นว่าง = ตัดออก</span>
                          )}
                        </div>
                        <textarea
                          value={val}
                          onChange={(e) => setBeat(b.fine_state, e.target.value)}
                          rows={2}
                          placeholder={b.example}
                        />
                      </div>
                    );
                  })}
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
