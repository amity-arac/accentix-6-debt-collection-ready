import { useEffect, useMemo, useRef, useState } from "react";
import { X, Wand2 } from "lucide-react";
import { fetchFlowBeats, createFlowCompany, type FlowBeat } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";

const MODAL_EXIT_MS = 380;
const BASE_COMPANY_NAME = "อิอ้อน"; // AEON name baked into the example templates

type Props = {
  open: boolean;
  onClose: () => void;
  /** Called after a company is created — (caseId, company) so the app can jump
   *  straight into a flow session with the new demo persona. */
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

  // Load the beats once (pre-fill each with the AEON example as a starting point).
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

  if (!mounted) return null;

  const setBeat = (fs: string, v: string) =>
    setTemplates((t) => ({ ...t, [fs]: v }));

  // Swap the baked-in AEON name for the entered company name across all beats.
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
      if (res.ok && res.case_id && res.company) {
        onCreated(res.case_id, res.company);
      } else {
        setErrors(res.errors ?? ["สร้างไม่สำเร็จ"]);
      }
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
        className="persona-modal flow-builder"
        role="dialog"
        aria-modal="true"
        aria-labelledby="flow-builder-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <header className="persona-modal-head">
          <h2 id="flow-builder-title" className="persona-modal-title">
            สร้างบริษัทใหม่ (Flow)
          </h2>
          <span className="persona-modal-count">
            {filledCount}/{beats.length} beats
          </span>
          <button
            type="button"
            className="persona-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <p className="persona-modal-note">
          กรอกชื่อบริษัท แล้วแก้ข้อความแต่ละจังหวะ (pre-fill ตัวอย่าง AEON ไว้ให้) —
          จังหวะที่เว้นว่างจะถูกตัดออกจาก flow เอง กด “ใส่ชื่อบริษัทให้อัตโนมัติ” เพื่อแทน
          “{BASE_COMPANY_NAME}” ด้วยชื่อคุณทุกช่อง
        </p>

        {errors.length > 0 && (
          <div className="mic-error" role="alert" style={{ margin: "0 16px 8px" }}>
            <span>{errors.join(" · ")}</span>
          </div>
        )}

        <div className="flow-builder-meta">
          <label>
            รหัสบริษัท (A–Z)
            <input
              value={company}
              onChange={(e) => setCompany(e.target.value.toUpperCase())}
              placeholder="เช่น SCB"
              maxLength={12}
            />
          </label>
          <label>
            ชื่อบริษัท (ไทย)
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="เช่น ไทยพาณิชย์"
            />
          </label>
          <label>
            ชื่อเจ้าหน้าที่ (ไทย)
            <input
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="เช่น น้องเอสซีบี"
            />
          </label>
          <button type="button" className="btn" onClick={applyName} disabled={!displayName.trim()}>
            <Wand2 size={14} aria-hidden="true" /> ใส่ชื่อบริษัทให้อัตโนมัติ
          </button>
        </div>

        <div className="persona-modal-body flow-builder-beats">
          {beats.map((b) => (
            <div key={b.fine_state} className="flow-beat">
              <div className="flow-beat-head">
                <code>{b.fine_state}</code>
                <span className="flow-beat-hint">{b.hint}</span>
              </div>
              <textarea
                value={templates[b.fine_state] ?? ""}
                onChange={(e) => setBeat(b.fine_state, e.target.value)}
                rows={2}
                placeholder={b.example}
              />
            </div>
          ))}
        </div>

        <div className="persona-detail-actions" style={{ padding: "12px 16px" }}>
          <button
            type="button"
            className="btn persona-select-btn"
            onClick={() => void submit()}
            disabled={saving || !company.trim() || !displayName.trim()}
          >
            {saving ? "กำลังสร้าง…" : "สร้าง + เล่นเลย"}
          </button>
        </div>
      </div>
    </div>
  );
}
