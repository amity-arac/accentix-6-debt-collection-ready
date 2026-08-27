import { useEffect, useRef, useState } from "react";
import { Play, Save, X } from "lucide-react";

type Props = {
  onRestart: () => void;
  onSave: () => void;
  saving: boolean;
};

export function EndOfCallCard({ onRestart, onSave, saving }: Props) {
  const btnRef = useRef<HTMLButtonElement | null>(null);
  // The card used to sit dead centre and cover the transcript — the one thing you
  // want in a screenshot of a finished call. It now docks to a corner and can be
  // dismissed outright; the call is over either way, so nothing is lost by hiding it.
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    btnRef.current?.focus();
  }, []);

  if (hidden) return null;

  return (
    <div
      className="end-of-call-card"
      role="dialog"
      aria-labelledby="end-of-call-title"
    >
      <button
        type="button"
        className="end-of-call-dismiss"
        onClick={() => setHidden(true)}
        title="ซ่อน (สายจบแล้ว)"
        aria-label="ซ่อนกล่องสายจบ"
      >
        <X size={14} aria-hidden="true" />
      </button>
      <h2 id="end-of-call-title" className="end-of-call-title">
        Call ended
      </h2>
      <div className="end-of-call-actions">
        <button
          type="button"
          className="btn end-of-call-save"
          onClick={onSave}
          disabled={saving}
        >
          <Save size={15} aria-hidden="true" /> {saving ? "Saving…" : "Save"}
        </button>
        <button
          ref={btnRef}
          type="button"
          className="btn end-of-call-action"
          onClick={onRestart}
        >
          <Play size={15} aria-hidden="true" /> New call
        </button>
      </div>
    </div>
  );
}
