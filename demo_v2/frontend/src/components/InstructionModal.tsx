import { useEffect, useRef, useState } from "react";
import { X, Copy, Check } from "lucide-react";
import { fetchFlowInstruction, COMPANY_LABELS } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";
import { t } from "../i18n";
import { useLang } from "../hooks/useLang";

const MODAL_EXIT_MS = 380;

type Props = {
  open: boolean;
  company: string | null;
  onClose: () => void;
};

export function InstructionModal({ open, company, onClose }: Props) {
  const { mounted, visible } = useMountTransition(open, MODAL_EXIT_MS);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!mounted || !company) return;
    setText(""); setErr(""); setCopied(false);
    void fetchFlowInstruction(company)
      .then(setText)
      .catch(() => setErr(t("instructionFailed", { company })));
  }, [mounted, company]);

  useEffect(() => {
    if (mounted) dialogRef.current?.focus();
  }, [mounted]);

  if (!mounted) return null;

  const copy = () => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className={`persona-modal-backdrop${visible ? " open" : ""}`} onClick={onClose} role="presentation">
      <div
        ref={dialogRef}
        className="fx-modal fx-instr"
        role="dialog" aria-modal="true" aria-labelledby="fx-instr-title" tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); e.stopPropagation(); }}
      >
        <div className="fx-head">
          <h2 id="fx-instr-title">
            Instruction — <span className="fx-accent">{company ? COMPANY_LABELS[company] ?? company : ""}</span>
          </h2>
          <span className="fx-step">{t("promptTitle")}</span>
          <button className="fx-btn fx-mini" onClick={copy} disabled={!text}>
            {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? t("copied") : t("copy")}
          </button>
          <button className="fx-x" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="fx-body">
          {err && <div className="fx-errors" role="alert">{err}</div>}
          <p className="fx-note" style={{ marginTop: 0 }}>
            {t("promptBlurb")} <code>[placeholder]</code> {t("filledAtCall")}
          </p>
          <pre className="fx-instr-pre">{text || t("loading")}</pre>
        </div>
        <div className="fx-foot">
          <span className="fx-foot-hint">{t("readOnly")}</span>
          <span className="fx-spacer" />
          <button className="fx-btn fx-primary" onClick={onClose}>{t("close")}</button>
        </div>
      </div>
    </div>
  );
}
