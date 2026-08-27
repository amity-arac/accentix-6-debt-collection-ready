import { useEffect, useRef } from "react";
import { Mic } from "lucide-react";
import { Bubble } from "./Bubble";
import { ChatEmptyHint } from "./ChatEmptyHint";
import { ThinkingDot } from "./ThinkingDot";
import type { BubbleEntry } from "../hooks/useSession";

type Props = {
  entries: BubbleEntry[];
  interim?: string;
  started: boolean;
  done: boolean;
  busy?: boolean;
};

export function ChatStream({ entries, interim, started, done, busy }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length, interim, busy]);

  const showEmptyHint = started && !done && entries.length === 0;

  return (
    <div className="chat-stream">
      <div
        className="chat-inner"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
      >
        {showEmptyHint && <ChatEmptyHint />}
        {entries.map((e) => (
          <Bubble key={e.id} entry={e} />
        ))}
        {interim && (
          <div className="bubble user interim" aria-live="off">
            <div className="bubble-text">{interim}</div>
            <div className="bubble-meta" aria-label="Transcribing">
              <Mic size={12} aria-hidden="true" /> …
            </div>
          </div>
        )}
        {busy && (
          <div
            className="bubble agent-reply thinking-bubble"
            aria-label="Agent is working"
          >
            <ThinkingDot />
          </div>
        )}
        <div ref={endRef} style={{ height: "24px" }} aria-hidden="true" />
      </div>
    </div>
  );
}
