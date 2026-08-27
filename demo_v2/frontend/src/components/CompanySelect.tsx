import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { COMPANY_LABELS, type PersonaCase } from "../api";

type Props = {
  companies: string[];
  cases: PersonaCase[];
  /** Codes the server will let us delete (Builder-created). */
  deletable?: string[];
  onPick: (company: string) => void;
  onNew: () => void;
  onDelete?: (company: string) => Promise<void> | void;
};

export function CompanySelect({ companies, cases, deletable = [], onPick, onNew, onDelete }: Props) {
  // Which card is asking "sure?" — a second click on the same card confirms, so a
  // stray click can never take a company down, and nothing blocks on window.confirm.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const info = (co: string) => {
    const rows = cases.filter((c) => c.company === co);
    return { count: rows.length, loan: rows[0]?.loan_type ?? "" };
  };

  const remove = async (co: string) => {
    setBusy(co);
    try {
      await onDelete?.(co);
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  };

  return (
    <div className="studio">
      <div className="studio-brand">
        <span className="studio-logo">F</span>
        <div>
          <h1>Flow Studio</h1>
          <span className="studio-tag">debt-collection agent</span>
        </div>
      </div>
      <p className="studio-step">เลือกบริษัทที่จะทำงานด้วย</p>
      <div className="co-grid">
        {companies.map((co) => {
          const { count, loan } = info(co);
          const canDelete = Boolean(onDelete) && deletable.includes(co);
          const asking = confirming === co;
          return (
            <div className="co-slot" key={co}>
              <button className="co-card" onClick={() => onPick(co)}>
                <span className="co-badge">{co.slice(0, 2)}</span>
                <span className="co-nm">{COMPANY_LABELS[co] ?? co}</span>
                <span className="co-sub">
                  {co}
                  {loan ? ` · ${loan}` : ""}
                </span>
                <span className="co-meta">
                  <span className="co-pill flow">flow</span>
                  <span className="co-pill">{count} personas</span>
                </span>
              </button>
              {canDelete && (
                <button
                  className={`co-del${asking ? " asking" : ""}`}
                  onClick={() => (asking ? void remove(co) : setConfirming(co))}
                  onBlur={() => setConfirming((c) => (c === co ? null : c))}
                  disabled={busy === co}
                  title={`ลบบริษัท ${co}`}
                  aria-label={`ลบบริษัท ${co}`}
                >
                  {busy === co ? "กำลังลบ…" : asking ? "ลบเลย?" : <Trash2 size={15} aria-hidden="true" />}
                </button>
              )}
            </div>
          );
        })}
        <button className="co-card add" onClick={onNew}>
          <span className="co-plus">
            <Plus size={22} aria-hidden="true" />
          </span>
          <span className="co-nm">สร้างบริษัทใหม่</span>
          <span className="co-sub">Flow Builder</span>
        </button>
      </div>
    </div>
  );
}
