/**
 * The language switch, fixed to the top-right of every screen.
 *
 * It sits outside the screens rather than inside the control bar because the
 * choice has to be available BEFORE a call exists — the landing page is already
 * written in one language or the other, and picking it after entering the
 * playground would mean reading Thai to find the English button.
 *
 * It changes two things at once, on purpose: the interface, and the language the
 * agent speaks on the next call. A Thai interface wrapped around an English call
 * (or the reverse) reads as a bug, so there is one control, not two.
 */
import { Languages } from "lucide-react";

import { useLang } from "../hooks/useLang";
import { getLang, setLangGlobal, t, type Lang } from "../i18n";

export function LangSwitch({
  onChange,
  disabled = false,
}: {
  /** Told after the switch, so the session hook can carry it to the server. */
  onChange?: (lang: Lang) => void;
  /** Locked mid-call: the catalog, voice and recogniser all change together. */
  disabled?: boolean;
}) {
  const lang = useLang();

  const pick = (next: Lang) => {
    if (next === getLang()) return;
    setLangGlobal(next);
    onChange?.(next);
  };

  return (
    <div className="lang-switch" role="group" aria-label="Interface and call language">
      <span className="lang-switch-icon" aria-hidden="true">
        <Languages size={13} />
      </span>
      <button
        type="button"
        className={`lang-switch-btn ${lang === "th" ? "on" : ""}`}
        onClick={() => pick("th")}
        disabled={disabled}
        aria-pressed={lang === "th"}
        title={t("langThTitle")}
      >
        ไทย
      </button>
      <button
        type="button"
        className={`lang-switch-btn ${lang === "en" ? "on" : ""}`}
        onClick={() => pick("en")}
        disabled={disabled}
        aria-pressed={lang === "en"}
        title={t("langEnTitle")}
      >
        EN
      </button>
    </div>
  );
}
