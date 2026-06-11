import { useState } from "react";
import { HelpCircle } from "lucide-react";

const SHORTCUTS: Array<[string, string]> = [
  ["Space", "Hold to talk"],
  ["Esc", "Skip TTS / cancel"],
  ["R", "Reset call"],
  ["K", "Toggle customer panel"],
  ["P", "Pause / resume"],
];

export function ShortcutsHint() {
  const [open, setOpen] = useState(false);

  return (
    <div className="shortcuts-hint">
      {open && (
        <div className="shortcuts-popover" role="dialog" aria-label="Shortcuts">
          <div className="shortcuts-title">Shortcuts</div>
          <dl className="shortcuts-list">
            {SHORTCUTS.map(([k, label]) => (
              <div className="shortcuts-row" key={k}>
                <dt>
                  <kbd>{k}</kbd>
                </dt>
                <dd>{label}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      <button
        type="button"
        className="shortcuts-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Hide shortcuts" : "Show shortcuts"}
        aria-expanded={open}
      >
        <HelpCircle size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
