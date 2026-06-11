import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, IdCard } from "lucide-react";
import type { Agent, CustomerData } from "../api";
import { renderDate, looksLikeCanonicalDate } from "../format/dateRender";

type Props = {
  caseId: string | null;
  mode: "replay" | "live" | null;
  agent: Agent | null;
  customer: CustomerData;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** When true the header acts as a button that opens the persona picker. */
  headerClickable?: boolean;
  onHeaderClick?: () => void;
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
  headerClickable = false,
  onHeaderClick,
}: Props) {
  // The card morphs its own size between full and the 52px pill (one element,
  // like the start bar morphs its width). When collapsed, the content area is
  // made inert so its buttons leave the tab order while faded out.
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (contentRef.current) contentRef.current.inert = collapsed;
  }, [collapsed]);

  // Measure the panel's natural height while expanded so collapse can morph
  // max-height to the exact value (no dead-zone, no clipping). scrollHeight
  // reports the true content height even while clamped, so it re-measures
  // correctly when the persona changes. Keyed on the data, not the toggle, so
  // it never samples mid-transition (when the width is still 52px).
  const shellRef = useRef<HTMLElement>(null);
  const [fullHeight, setFullHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (collapsed || !shellRef.current) return;
    setFullHeight(shellRef.current.scrollHeight + 2); // + 1px top/bottom border
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer, caseId, mode, agent]);

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

  const headerInner = (
    <>
      <span className="company">{String(customer.company_name ?? "AEON")}</span>
      <span className="case-id">{caseId ?? "—"}</span>
      {headerClickable && (
        <ChevronDown
          size={14}
          className="panel-head-chevron"
          aria-hidden="true"
        />
      )}
      <span className={`mode-pill ${mode ?? ""}`}>
        <span className="mode-dot" aria-hidden="true" />
        {mode ? (agent ? `${mode} · ${agent}` : mode) : "…"}
      </span>
    </>
  );

  return (
    <aside
      ref={shellRef}
      className={`customer-panel ${collapsed ? "is-collapsed" : ""}`}
      style={
        collapsed
          ? undefined
          : fullHeight != null
            ? { maxHeight: `${fullHeight}px` }
            : undefined
      }
    >
      <button
        type="button"
        className="panel-mini-icon"
        onClick={onToggleCollapse}
        aria-label="Show customer details"
        title="Show customer details"
        aria-hidden={!collapsed}
        tabIndex={collapsed ? 0 : -1}
      >
        <IdCard size={22} aria-hidden="true" />
      </button>

      <div className="panel-content" ref={contentRef}>
        {headerClickable ? (
            <button
              type="button"
              className="panel-head panel-head-trigger"
              onClick={onHeaderClick}
              aria-label="Switch persona"
              title="Switch persona"
            >
              {headerInner}
            </button>
          ) : (
            <header className="panel-head">{headerInner}</header>
          )}

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
      </div>
    </aside>
  );
}
