import { useEffect, useRef, useState } from "react";
import { X, Download, Upload, FileJson } from "lucide-react";
import { createFlowCompanyRaw } from "../api";
import { useMountTransition } from "../hooks/useMountTransition";
import { t } from "../i18n";
import { useLang } from "../hooks/useLang";

const MODAL_EXIT_MS = 380;

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (caseId: string, company: string) => void;
};

// New-company flow = DOWNLOAD a template, fill it, UPLOAD it back. No in-app JSON editor.
// Tools in the template are HTTP webhooks (impl:"http") → every tool call fires an API.
export function FlowUploadModal({ open, onClose, onCreated }: Props) {
  const { mounted, visible } = useMountTransition(open, MODAL_EXIT_MS);
  const [fileName, setFileName] = useState<string>("");
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (mounted) dialogRef.current?.focus();
  }, [mounted]);
  useEffect(() => {
    if (!open) { setFileName(""); setErrors([]); }
  }, [open]);

  if (!mounted) return null;

  const downloadTemplate = async () => {
    setErrors([]);
    try {
      const resp = await fetch("/api/flow/template");
      if (!resp.ok) throw new Error(`/api/flow/template ${resp.status}`);
      const tpl = await resp.json();
      const blob = new Blob([JSON.stringify(tpl, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "new_company_template.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setErrors([t("templateFailed", { err: String(e?.message ?? e) })]);
    }
  };

  const onFile = async (file: File) => {
    setErrors([]);
    setFileName(file.name);
    setSaving(true);
    try {
      const text = await file.text();
      let doc: any;
      try {
        doc = JSON.parse(text);
      } catch (e: any) {
        setErrors([t("notJson", { err: String(e?.message ?? e) })]);
        setSaving(false);
        return;
      }
      const spec = doc.spec ?? doc;             // accept {spec,catalog} or a bare spec
      const catalog = doc.catalog ?? [];
      const res = await createFlowCompanyRaw({
        spec,
        catalog,
        display_name: doc.display_name ?? "",
        agent_name: doc.agent_name ?? "",
        crm: doc.crm ?? undefined,
      });
      if (res.ok && res.case_id && res.company) {
        // Created, but some sentence names data this company cannot fill: say so here,
        // the last moment the person who wrote the template is still looking.
        if (res.warning) setErrors([res.warning]);
        onCreated(res.case_id, res.company);
      } else setErrors(res.errors ?? [t("createFailed")]);
    } catch (e: any) {
      setErrors([t("uploadFailed", { err: String(e?.message ?? e) })]);
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
        className="fx-modal fx-upload"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fx-upload-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <div className="fx-head">
          <h2 id="fx-upload-title">{t("uploadTitle")}</h2>
          <span className="fx-step">{t("uploadSteps")}</span>
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

          <ol className="fx-upload-steps">
            <li>
              <b>1.</b> {t("uploadStep1")}
              <div>
                <button className="fx-btn fx-ghost" onClick={() => void downloadTemplate()} disabled={saving}>
                  <Download size={15} aria-hidden="true" /> {t("downloadTemplate")}
                </button>
              </div>
            </li>
            <li>
              <b>2.</b> {t("uploadStep2")}
            </li>
            <li>
              <b>3.</b> {t("uploadStep3")}
              <div>
                <button className="fx-btn fx-primary" onClick={() => fileRef.current?.click()} disabled={saving}>
                  <Upload size={15} aria-hidden="true" /> {saving ? t("creating") : t("uploadJson")}
                </button>
                {fileName && (
                  <span className="fx-upload-file">
                    <FileJson size={13} aria-hidden="true" /> {fileName}
                  </span>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onFile(f);
                    e.target.value = "";
                  }}
                />
              </div>
            </li>
          </ol>
        </div>
      </div>
    </div>
  );
}
