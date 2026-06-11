import { useEffect, useRef } from "react";
import { Play } from "lucide-react";

type Props = {
  onRestart: () => void;
};

export function EndOfCallCard({ onRestart }: Props) {
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
      <button
        ref={btnRef}
        type="button"
        className="btn end-of-call-action"
        onClick={onRestart}
      >
        <Play size={16} aria-hidden="true" /> Start a new call
      </button>
    </div>
  );
}
