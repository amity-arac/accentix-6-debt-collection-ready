import { useEffect, useState } from "react";
import { Check, CornerDownRight, Mic, VolumeX, X as XIcon } from "lucide-react";
import type { BubbleEntry } from "../hooks/useSession";
import {
  describeTool,
  friendlyResultSummary,
  toolCategory,
  RESULT_KEY_LABEL,
} from "./ToolMeta";
import {
  looksLikeCanonicalDate,
  looksLikeCanonicalDateTime,
  renderDate,
  renderDateTime,
} from "../format/dateRender";

type Props = { entry: BubbleEntry };

const TOOL_COLOR: Record<string, string> = {
  verify_identity: "var(--tool-blue)",
  check_account_status: "var(--tool-gray)",
  get_current_datetime: "var(--tool-gray)",
  record_verbal_commitment: "var(--tool-purple)",
  payment_date: "var(--tool-green)",
  callback_datetime: "var(--tool-green)",
};

const CATEGORY_COLOR: Record<string, string> = {
  KYC: "var(--tool-blue)",
  READ: "var(--tool-gray)",
  WRITE: "var(--tool-green)",
};

const ARGS_WRAP_THRESHOLD = 50;

function fmtArg(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return `"${v}"`;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function fmtNumber(n: number): string {
  return n.toLocaleString("en-US");
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return (
    typeof v === "object" &&
    v !== null &&
    !Array.isArray(v) &&
    Object.values(v).every(
      (x) =>
        x === null ||
        typeof x === "string" ||
        typeof x === "number" ||
        typeof x === "boolean",
    )
  );
}

function renderValue(v: unknown): React.ReactNode {
  if (v === null || v === undefined) {
    return <span className="kv-null">—</span>;
  }
  if (typeof v === "boolean") {
    return v ? (
      <Check size={14} className="kv-true" aria-label="true" />
    ) : (
      <XIcon size={14} className="kv-false" aria-label="false" />
    );
  }
  if (typeof v === "number") {
    return <span>{fmtNumber(v)}</span>;
  }
  if (typeof v === "string") {
    if (looksLikeCanonicalDateTime(v)) return <span>{renderDateTime(v)}</span>;
    if (looksLikeCanonicalDate(v)) return <span>{renderDate(v)}</span>;
    return <span>{v}</span>;
  }
  return <span>{JSON.stringify(v)}</span>;
}

function templateCountLabel(ids: number[]): string {
  return `${ids.length} template${ids.length === 1 ? "" : "s"} used — click to inspect`;
}

let cachedTemplates: Record<string, string> | null = null;
let templatesLoadingPromise: Promise<Record<string, string>> | null = null;

async function loadTemplates(): Promise<Record<string, string>> {
  if (cachedTemplates) return cachedTemplates;
  if (templatesLoadingPromise) return templatesLoadingPromise;
  templatesLoadingPromise = import("../data/v6_templates.json").then(
    (mod) => {
      cachedTemplates = mod.default as Record<string, string>;
      return cachedTemplates;
    },
  );
  return templatesLoadingPromise;
}

function TextIdsReveal({ ids }: { ids: number[] }) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<Record<string, string> | null>(
    cachedTemplates,
  );

  useEffect(() => {
    if (!open || templates) return;
    let cancelled = false;
    void loadTemplates().then((t) => {
      if (!cancelled) setTemplates(t);
    });
    return () => {
      cancelled = true;
    };
  }, [open, templates]);

  return (
    <>
      <button
        type="button"
        className="text-ids"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={templateCountLabel(ids)}
      >
        [{ids.join(", ")}]
      </button>
      {open && (
        <dl className="text-ids-panel">
          {ids.map((id) => (
            <div className="text-ids-row" key={id}>
              <dt>{id}</dt>
              <dd>
                {templates
                  ? templates[String(id)] ?? <em>(template not found)</em>
                  : <em>Loading…</em>}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

function ToolResultBody({ result }: { result: unknown }) {
  if (isPlainObject(result)) {
    const entries = Object.entries(result);
    return (
      <dl className="tool-result-table">
        {entries.map(([k, v]) => (
          <div className="kv-row" key={k}>
            <dt>{RESULT_KEY_LABEL[k] ?? k}</dt>
            <dd>{renderValue(v)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <pre className="tool-result-body">{JSON.stringify(result, null, 2)}</pre>
  );
}

export function Bubble({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (entry.kind === "user") {
    return (
      <div className="bubble user">
        <div className="bubble-text">{entry.text}</div>
        {entry.viaMic && (
          <div className="bubble-meta" aria-label="Sent via microphone">
            <Mic size={12} aria-hidden="true" />
          </div>
        )}
      </div>
    );
  }

  if (entry.kind === "reply") {
    return (
      <div className={`bubble agent-reply ${entry.speaking ? "speaking" : ""}`}>
        <div className="bubble-text">{entry.text}</div>
        {entry.ttsFailed && (
          <div className="tts-failed" aria-label="Audio playback failed">
            <VolumeX size={12} aria-hidden="true" /> Audio playback failed
          </div>
        )}
        {entry.text_ids.length > 0 && (
          <div className="bubble-meta">
            <TextIdsReveal ids={entry.text_ids} />
          </div>
        )}
      </div>
    );
  }

  if (entry.kind === "tool_call") {
    const argEntries = Object.entries(entry.args);
    const inlineStr = argEntries
      .map(([k, v]) => `${k}: ${fmtArg(v)}`)
      .join(", ");
    const wrap = inlineStr.length > ARGS_WRAP_THRESHOLD;
    const category = toolCategory(entry.name);

    return (
      <div className="bubble tool-call" title={describeTool(entry.name)}>
        <span
          className="tool-dot"
          aria-hidden="true"
          style={{ background: TOOL_COLOR[entry.name] ?? "var(--tool-gray)" }}
        />
        <code className="tool-sig">
          <span className="tool-name">{entry.name}</span>
          {category && (
            <span
              className="tool-category"
              style={{ color: CATEGORY_COLOR[category], borderColor: CATEGORY_COLOR[category] }}
            >
              {category}
            </span>
          )}
          {!wrap && (
            <>
              (<span className="tool-args">{inlineStr}</span>)
            </>
          )}
          {wrap && (
            <div className="tool-args-block">
              {argEntries.map(([k, v]) => (
                <div className="tool-arg" key={k}>
                  <span className="tool-arg-key">{k}:</span>{" "}
                  <span className="tool-arg-val">{fmtArg(v)}</span>
                </div>
              ))}
            </div>
          )}
        </code>
      </div>
    );
  }

  // tool_result
  const r = entry.result as any;
  const ok = r && typeof r === "object" && (r.verified === true || r.recorded === true);
  const idTag = r && typeof r === "object" && typeof r.id === "string" ? `id: "${r.id}"` : null;
  const friendly = friendlyResultSummary(r);
  const summary = friendly ?? (typeof r === "object" ? "result" : String(r));

  return (
    <button
      type="button"
      className={`bubble tool-result ${ok ? "ok" : ""}`}
      onClick={() => setExpanded((e) => !e)}
      aria-expanded={expanded}
      title="Click to expand"
    >
      <span className="tool-glyph" aria-hidden="true">
        <CornerDownRight size={14} />
      </span>
      <code className="tool-sig">
        <span className="tool-result-summary">{summary}</span>
        {idTag && <span className="tool-result-id"> · {idTag}</span>}
        <span className="tool-result-toggle">{expanded ? " ▴" : " ▾"}</span>
      </code>
      {expanded && <ToolResultBody result={r} />}
    </button>
  );
}
