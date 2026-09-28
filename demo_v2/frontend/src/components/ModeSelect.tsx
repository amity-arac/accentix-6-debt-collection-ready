import { Headphones, FileText } from "lucide-react";
import { COMPANY_LABELS } from "../api";
import { t } from "../i18n";
import { useLang } from "../hooks/useLang";

type Props = {
  company: string;
  onPlay: () => void;
  onView: () => void;
  onBack: () => void;
};

export function ModeSelect({ company, onPlay, onView, onBack }: Props) {
  const label = COMPANY_LABELS[company] ?? company;
  return (
    <div className="studio">
      <div className="studio-crumb">
        <button onClick={onBack}>{t("company")}</button>
        <span className="sep">›</span>
        <b>
          {label} ({company})
        </b>
      </div>
      <p className="studio-step">{t("whatToDo", { label })}</p>
      <div className="mode-grid">
        <button className="mode-card" onClick={onPlay}>
          <span className="mode-ic">
            <Headphones size={26} aria-hidden="true" />
          </span>
          <span className="mode-t">Playground</span>
          <span className="mode-d">
            {t("playgroundBlurb")}
          </span>
          <span className="mode-go">{t("enterPlayground")}</span>
        </button>
        <button className="mode-card" onClick={onView}>
          <span className="mode-ic">
            <FileText size={24} aria-hidden="true" />
          </span>
          <span className="mode-t">{t("readInstruction")}</span>
          <span className="mode-d">
            {t("instructionBlurb")}
          </span>
          <span className="mode-go">{t("open")}</span>
        </button>
      </div>
    </div>
  );
}
