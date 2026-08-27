import { useEffect, useRef } from "react";

type Props = {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ResetConfirmModal({ open, onCancel, onConfirm }: Props) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    prevFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    confirmRef.current?.focus();
    return () => {
      prevFocusRef.current?.focus();
    };
  }, [open]);

  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Local handling so the global keyboard handler never fires while we're open.
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key === "Enter") {
      // Only fire confirm if the focused element isn't already a button
      // (a focused button handles Enter via its own click handler).
      if (!(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        onConfirm();
      }
      return;
    }
    if (e.key === "Tab") {
      // Trap focus between cancel and confirm.
      e.preventDefault();
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === cancelRef.current) confirmRef.current?.focus();
        else cancelRef.current?.focus();
      } else {
        if (active === confirmRef.current) cancelRef.current?.focus();
        else confirmRef.current?.focus();
      }
    }
  };

  return (
    <div
      className="reset-modal-backdrop"
      onClick={onCancel}
      onKeyDown={handleKeyDown}
      role="presentation"
    >
      <div
        className="reset-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reset-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="reset-modal-title" className="reset-modal-title">
          Reset conversation?
        </h2>
        <p className="reset-modal-body">
          This will clear the history and start over.
        </p>
        <div className="reset-modal-actions">
          <button
            ref={cancelRef}
            type="button"
            className="btn reset-modal-cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="btn reset-modal-confirm"
            onClick={onConfirm}
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
