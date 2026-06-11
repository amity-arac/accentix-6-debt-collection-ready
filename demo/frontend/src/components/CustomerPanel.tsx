import { IdCard } from "lucide-react";
import type { Agent, CustomerData } from "../api";
import { renderDate, looksLikeCanonicalDate } from "../format/dateRender";

type Props = {
  caseId: string | null;
  mode: "replay" | "live" | null;
  agent: Agent | null;
  customer: CustomerData;
  collapsed: boolean;
  onToggleCollapse: () => void;
};

function fmtAmount(v: unknown): string {
  if (typeof v !== "number") return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

const STATUS_LABEL: Record<string, string> = {
  normal: "Account normal",
  pending_review: "Under review",
  closed: "Closed",
};

export function CustomerPanel({
  caseId,
  mode,
  agent,
  customer,
  collapsed,
  onToggleCollapse,
}: Props) {
  if (collapsed) {
    return (
      <button
        className="customer-panel collapsed"
        onClick={onToggleCollapse}
        aria-label="Show customer details"
        title="Show customer details"
      >
        <IdCard size={22} aria-hidden="true" />
      </button>
    );
  }

  const rawDue = customer.due_date;
  const dueDateLabel = looksLikeCanonicalDate(rawDue)
    ? renderDate(String(rawDue))
    : rawDue
      ? String(rawDue)
      : null;

  const caseStatus = String(customer.case_status ?? "normal");
  const caseStatusLabel = STATUS_LABEL[caseStatus] ?? caseStatus;
  const note = customer.case_status_note;

  const loanType = customer.loan_type ? String(customer.loan_type) : null;
  const last4 = customer.last_4_digits ? String(customer.last_4_digits) : null;
  const customerName = customer.customer_name
    ? String(customer.customer_name)
    : "—";
  const phone = customer.customer_phone
    ? String(customer.customer_phone)
    : null;

  const balanceMetaParts: string[] = [];
  if (typeof customer.minimum_payment_due === "number") {
    balanceMetaParts.push(`Min ${fmtAmount(customer.minimum_payment_due)}`);
  }
  if (dueDateLabel) balanceMetaParts.push(`Due ${dueDateLabel}`);
  if (customer.due_status) balanceMetaParts.push(String(customer.due_status));

  return (
    <aside className="customer-panel">
      <header className="panel-head">
        <span className="company">{String(customer.company_name ?? "AEON")}</span>
        <span className="case-id">{caseId ?? "—"}</span>
        <span className={`mode-pill ${mode ?? ""}`}>
          <span className="mode-dot" aria-hidden="true" />
          {mode ? (agent ? `${mode} · ${agent}` : mode) : "…"}
        </span>
      </header>

      <section className="panel-identity">
        <h1 className="name-display" title={customerName}>
          {customerName}
        </h1>
        {(loanType || last4) && (
          <p className="name-sub">
            {loanType ?? "—"}
            {last4 && (
              <>
                <span className="dot-sep" aria-hidden="true">·</span>
                ending {last4}
              </>
            )}
          </p>
        )}
      </section>

      <section className="panel-balance" aria-label="Balance">
        <div className="balance-line">
          <span className="balance-numeral">
            {fmtAmount(customer.total_amount_due)}
          </span>
          <span className="balance-currency">THB</span>
        </div>
        {balanceMetaParts.length > 0 && (
          <p className="balance-meta">{balanceMetaParts.join(" · ")}</p>
        )}
      </section>

      <section className={`panel-case status-${caseStatus}`}>
        <span className="status-pill">
          <span className="status-pill-dot" aria-hidden="true" />
          {caseStatusLabel}
        </span>
        {note && <p className="panel-note">{String(note)}</p>}
      </section>

      {phone && (
        <footer className="panel-footer">
          <span className="panel-phone-label">Phone</span>
          <span className="panel-phone">{phone}</span>
        </footer>
      )}

      <button
        className="customer-panel-collapse"
        onClick={onToggleCollapse}
        aria-label="Hide customer details"
      >
        ▾ Collapse
      </button>
    </aside>
  );
}
