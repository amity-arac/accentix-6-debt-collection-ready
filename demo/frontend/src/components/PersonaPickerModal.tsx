import { useEffect, useMemo, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import type { PersonaCase } from "../api";

type Props = {
  open: boolean;
  cases: PersonaCase[];
  currentCaseId: string | null;
  onClose: () => void;
  onSelect: (caseId: string) => void;
};

type CompanyFilter = "All" | string;
type TrackFilter = "All" | "A" | "B";

// Fixed display order; only companies actually present are rendered as chips.
const COMPANY_ORDER = ["AEON", "AIS", "JAI", "KS"];

function fmtTHB(v: unknown, decimals = 0): string {
  if (typeof v !== "number") return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function trackShort(t: string | null): string {
  if (t === "Track_A") return "A";
  if (t === "Track_B") return "B";
  return "—";
}

export function PersonaPickerModal({
  open,
  cases,
  currentCaseId,
  onClose,
  onSelect,
}: Props) {
  const [companyFilter, setCompanyFilter] = useState<CompanyFilter>("All");
  const [trackFilter, setTrackFilter] = useState<TrackFilter>("All");
  const [selectedId, setSelectedId] = useState<string | null>(currentCaseId);

  const dialogRef = useRef<HTMLDivElement | null>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  // On open: reset selection to the current persona and focus the dialog.
  useEffect(() => {
    if (!open) return;
    setSelectedId(currentCaseId);
    prevFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    dialogRef.current?.focus();
    return () => {
      prevFocusRef.current?.focus();
    };
  }, [open, currentCaseId]);

  const companies = useMemo(
    () => COMPANY_ORDER.filter((c) => cases.some((x) => x.company === c)),
    [cases],
  );

  const filtered = useMemo(
    () =>
      cases.filter((c) => {
        if (companyFilter !== "All" && c.company !== companyFilter) return false;
        if (trackFilter === "A" && c.eval_track !== "Track_A") return false;
        if (trackFilter === "B" && c.eval_track !== "Track_B") return false;
        return true;
      }),
    [cases, companyFilter, trackFilter],
  );

  const selected = useMemo(
    () => cases.find((c) => c.id === selectedId) ?? null,
    [cases, selectedId],
  );

  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      className="persona-modal-backdrop"
      onClick={onClose}
      onKeyDown={handleKeyDown}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="persona-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="persona-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="persona-modal-head">
          <h2 id="persona-modal-title" className="persona-modal-title">
            Choose a persona
          </h2>
          <span className="persona-modal-count">{cases.length} personas</span>
          <button
            type="button"
            className="persona-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="persona-filters">
          <div className="persona-filter-group" role="group" aria-label="Company">
            <button
              type="button"
              className={`persona-chip ${companyFilter === "All" ? "on" : ""}`}
              onClick={() => setCompanyFilter("All")}
            >
              All
            </button>
            {companies.map((c) => (
              <button
                key={c}
                type="button"
                className={`persona-chip ${companyFilter === c ? "on" : ""}`}
                onClick={() => setCompanyFilter(c)}
              >
                {c}
              </button>
            ))}
          </div>
          <div className="persona-filter-group" role="group" aria-label="Track">
            {(["All", "A", "B"] as TrackFilter[]).map((t) => (
              <button
                key={t}
                type="button"
                className={`persona-chip track ${trackFilter === t ? "on" : ""}`}
                onClick={() => setTrackFilter(t)}
              >
                {t === "All" ? "All tracks" : `Track ${t}`}
              </button>
            ))}
          </div>
        </div>

        <div className="persona-modal-body">
          <ul className="persona-list" aria-label="Personas">
            {filtered.length === 0 && (
              <li className="persona-list-empty">No personas match.</li>
            )}
            {filtered.map((c) => {
              const isSelected = c.id === selectedId;
              const isCurrent = c.id === currentCaseId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    className={`persona-list-item${isSelected ? " selected" : ""}${
                      isCurrent ? " current" : ""
                    }`}
                    onClick={() => setSelectedId(c.id)}
                    aria-pressed={isSelected}
                  >
                    <span className="persona-item-title">
                      {c.topic || c.id}
                      {isCurrent && (
                        <Check
                          size={13}
                          className="persona-item-current"
                          aria-label="Current persona"
                        />
                      )}
                    </span>
                    <span className="persona-item-sub">
                      {c.customer_name ?? "—"} · ฿{fmtTHB(c.total_amount_due)}
                    </span>
                    <span className="persona-item-badges">
                      <span className="persona-company-tag">{c.company}</span>
                      <span
                        className={`persona-track-tag track-${trackShort(c.eval_track)}`}
                      >
                        Track {trackShort(c.eval_track)}
                      </span>
                      {typeof c.patience === "number" && (
                        <span className="persona-patience-tag">
                          patience {c.patience}/5
                        </span>
                      )}
                      {c.case_status && c.case_status !== "normal" && (
                        <span className="persona-status-tag">
                          {c.case_status.replace("_", " ")}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="persona-detail">
            {selected ? (
              <>
                <div className="persona-detail-scroll">
                  <h3 className="persona-detail-topic">
                    {selected.topic || selected.id}
                  </h3>
                  <p className="persona-detail-meta">
                    <span className="persona-company-tag">{selected.company}</span>
                    <span
                      className={`persona-track-tag track-${trackShort(selected.eval_track)}`}
                    >
                      Track {trackShort(selected.eval_track)}
                    </span>
                    {typeof selected.patience === "number" && (
                      <span className="persona-patience-tag">
                        patience {selected.patience}/5
                      </span>
                    )}
                    <span className="persona-detail-id">{selected.id}</span>
                  </p>

                  <h4 className="persona-section-label">Account</h4>
                  <dl className="persona-account-grid">
                    <dt>Customer</dt>
                    <dd>{selected.customer_name ?? "—"}</dd>
                    <dt>Loan</dt>
                    <dd>{selected.loan_type ?? "—"}</dd>
                    <dt>Balance</dt>
                    <dd>฿{fmtTHB(selected.total_amount_due, 2)} THB</dd>
                    <dt>Min. payment</dt>
                    <dd>฿{fmtTHB(selected.minimum_payment_due, 2)}</dd>
                    <dt>Due</dt>
                    <dd>
                      {selected.due_date ?? "—"}
                      {selected.due_status ? ` · ${selected.due_status}` : ""}
                    </dd>
                    <dt>Phone</dt>
                    <dd>{selected.customer_phone ?? "—"}</dd>
                    <dt>Last 4 (KYC)</dt>
                    <dd className="persona-kyc">{selected.last_4_digits ?? "—"}</dd>
                    <dt>Status</dt>
                    <dd>
                      {(selected.case_status ?? "normal").replace("_", " ")}
                      {selected.case_status_note
                        ? ` — ${selected.case_status_note}`
                        : ""}
                    </dd>
                  </dl>

                  <h4 className="persona-section-label">
                    Scenario <span>(your role)</span>
                  </h4>
                  {selected.persona && (
                    <p className="persona-scenario-persona">{selected.persona}</p>
                  )}
                  {selected.situation && (
                    <p className="persona-scenario">{selected.situation}</p>
                  )}
                  {selected.constraints && (
                    <>
                      <h5 className="persona-section-sublabel">Constraints</h5>
                      <p className="persona-scenario">{selected.constraints}</p>
                    </>
                  )}
                </div>

                <div className="persona-detail-actions">
                  <button
                    type="button"
                    className="btn persona-select-btn"
                    onClick={() => onSelect(selected.id)}
                    disabled={selected.id === currentCaseId}
                  >
                    {selected.id === currentCaseId
                      ? "Current persona"
                      : "Talk to this persona"}
                  </button>
                </div>
              </>
            ) : (
              <div className="persona-detail-empty">
                Select a persona to see their details.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
