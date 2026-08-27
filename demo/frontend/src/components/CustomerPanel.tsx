import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, IdCard } from "lucide-react";
import type { Engine, CustomerData } from "../api";
import { renderDate, looksLikeCanonicalDate } from "../format/dateRender";

type Props = {
  caseId: string | null;
  mode: "replay" | "live" | null;
  agent: Engine | null;
  customer: CustomerData;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** When true the header acts as a button that opens the persona picker. */
  headerClickable?: boolean;
  onHeaderClick?: () => void;
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

  const customerName = customer.customer_name
    ? String(customer.customer_name)
    : "—";
  const phone = customer.customer_phone
    ? String(customer.customer_phone)
    : null;

  // The record, as the API returned it. This panel used to render a fixed set of
  // debt-collection columns — loan type, balance, minimum payment, last 4, due status
  // — so a shop or a clinic showed a dash in every slot while the agent quoted real
  // values from the same call. There is no shape every tenant shares, so nothing is
  // interpreted: the row is listed, with dates rendered the way the agent says them.
  const HIDDEN = new Set(["customer_name", "customer_phone", "company_name", "msisdn"]);
  const rows = Object.entries(customer)
    .filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [
      k,
      looksLikeCanonicalDate(v) ? renderDate(String(v)) : String(v),
    ] as [string, string]);

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
          </section>

          <section className="panel-crm" aria-label="CRM record">
            <dl className="crm-rows">
              {rows.map(([k, v]) => (
                <div className="crm-row" key={k}>
                  <dt>{k}</dt>
                  <dd title={v}>{v}</dd>
                </div>
              ))}
            </dl>
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
