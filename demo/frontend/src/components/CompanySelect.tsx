import { Plus } from "lucide-react";
import { COMPANY_LABELS, type PersonaCase } from "../api";

type Props = {
  companies: string[];
  cases: PersonaCase[];
  onPick: (company: string) => void;
  onNew: () => void;
};

export function CompanySelect({ companies, cases, onPick, onNew }: Props) {
  const info = (co: string) => {
    const rows = cases.filter((c) => c.company === co);
    return { count: rows.length, loan: rows[0]?.loan_type ?? "" };
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
          return (
            <button className="co-card" key={co} onClick={() => onPick(co)}>
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
