import { useEffect, useRef } from "react";
import { Play, Save } from "lucide-react";

type Props = {
  onRestart: () => void;
  onSave: () => void;
  saving: boolean;
};

export function EndOfCallCard({ onRestart, onSave, saving }: Props) {
  const btnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    btnRef.current?.focus();
  }, []);

  return (
    <div
      className="end-of-call-card"
      role="dialog"
      aria-labelledby="end-of-call-title"
    >
      <h2 id="end-of-call-title" className="end-of-call-title">
        Call ended
      </h2>
      <p className="end-of-call-body">The session reached completion.</p>
      <div className="end-of-call-actions">
        <button
          type="button"
          className="btn end-of-call-save"
          onClick={onSave}
          disabled={saving}
        >
          <Save size={16} aria-hidden="true" /> {saving ? "Saving…" : "Save conversation"}
        </button>
        <button
          ref={btnRef}
          type="button"
          className="btn end-of-call-action"
          onClick={onRestart}
        >
          <Play size={16} aria-hidden="true" /> Start a new call
        </button>
      </div>
    </div>
  );
}
