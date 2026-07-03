import { useEffect, useRef, useState } from "react";

type Props = {
  open: boolean;
  saving: boolean;
  onConfirm: (comment: string) => void;
  onCancel: () => void;
};

/* Save dialog: a tester types an optional note about the call, then confirms to
 * persist the trajectory (the note rides along in the saved JSON's `comment`).
 * Modeled on ResetConfirmModal — same backdrop/focus-restore/Esc behavior, plus
 * a textarea and Cmd/Ctrl+Enter to confirm. The note is optional: Save works
 * with an empty field. */
export function SaveDialog({ open, saving, onConfirm, onCancel }: Props) {
  const [comment, setComment] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setComment("");
    prevFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    textareaRef.current?.focus();
    return () => {
      prevFocusRef.current?.focus();
    };
  }, [open]);

  if (!open) return null;

  const confirm = () => {
    if (saving) return;
    onConfirm(comment.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Local handling so the global keyboard handler never fires while we're open.
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      confirm();
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
        className="reset-modal save-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="save-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="save-dialog-title" className="reset-modal-title">
          Save conversation
        </h2>
        <p className="reset-modal-body save-dialog-body">
          Add an optional note about this test call.
        </p>
        <textarea
          ref={textareaRef}
          className="save-dialog-textarea"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Optional note about this call… (e.g. agent skipped KYC)"
          rows={4}
          disabled={saving}
        />
        <div className="reset-modal-actions">
          <button
            type="button"
            className="btn reset-modal-cancel"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn save-dialog-confirm"
            onClick={confirm}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
