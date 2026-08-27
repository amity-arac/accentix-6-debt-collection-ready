import { Mic } from "lucide-react";

export function ChatEmptyHint() {
  return (
    <div className="chat-empty-hint" aria-live="off">
      <Mic size={14} aria-hidden="true" />
      <span>Hold Space to talk — or type a message below</span>
    </div>
  );
}
